"""Build the authorized public full-text knowledge database."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from common import ROOT, norm, tokenize
from knowledge import digest, validate_catalog

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE knowledge_entries (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
 status TEXT NOT NULL, source TEXT NOT NULL, attribution TEXT NOT NULL,
 duplicate_of TEXT REFERENCES knowledge_entries(id) DEFERRABLE INITIALLY DEFERRED,
 metadata_json TEXT NOT NULL
);
CREATE TABLE knowledge_names (
 entry_id TEXT NOT NULL REFERENCES knowledge_entries(id),
 name TEXT NOT NULL, name_norm TEXT NOT NULL, PRIMARY KEY(entry_id,name)
);
CREATE INDEX knowledge_names_norm ON knowledge_names(name_norm);
CREATE TABLE knowledge_tokens (
 token TEXT NOT NULL, kind TEXT NOT NULL,
 entry_id TEXT NOT NULL REFERENCES knowledge_entries(id),
 PRIMARY KEY(token,kind,entry_id)
);
CREATE INDEX knowledge_tokens_token ON knowledge_tokens(token);
CREATE TABLE knowledge_chunks (
 entry_id TEXT NOT NULL REFERENCES knowledge_entries(id),
 seq INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(entry_id,seq)
);
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


def chunks(text, size=1200):
    text = re.sub(r'(?m)^\s*\|?[ \t|:\-]{12,}\|?\s*$', '', text)
    block = ''
    for line in text.splitlines():
        if len(block)+len(line)+1 > size and block:
            yield block.strip()
            block = ''
        while len(line) > size:
            if block:
                yield block.strip()
                block = ''
            yield line[:size]
            line = line[size:]
        block += line+'\n'
    if block.strip():
        yield block.strip()


def build(out_dir):
    out_dir=Path(out_dir)
    docs=validate_catalog()
    out_dir.mkdir(parents=True,exist_ok=True)
    handle,path=tempfile.mkstemp(prefix='knowledge-',suffix='.sqlite',dir=out_dir)
    os.close(handle)
    path=Path(path)
    db=sqlite3.connect(path)
    scope='public-fulltext'
    source_commit=os.environ.get('GITHUB_SHA','working-tree')
    counts={'entries':len(docs),'confirmed':sum(d['review']['status']=='confirmed' for _,d in docs),
            'chunks':0,'body_entries':0,'duplicates':sum('duplicate_of' in d for _,d in docs)}
    try:
        db.executescript(SCHEMA)
        for _,doc in docs:
            entry_id=doc['id']
            db.execute('INSERT INTO knowledge_entries VALUES (?,?,?,?,?,?,?,?)',(
                entry_id,doc['title'],doc['category'],doc['review']['status'],
                doc['provenance']['source'],doc['provenance']['attribution'],
                doc.get('duplicate_of'),json.dumps(doc,ensure_ascii=False)))
            names={doc['title'],*doc['aliases']}
            for name in sorted(names):
                db.execute('INSERT INTO knowledge_names VALUES (?,?,?)',(entry_id,name,norm(name)))
            tokens=set().union(*(tokenize(name) for name in names))
            tokens.update(set().union(*(tokenize(tag) for tag in doc['tags'])))
            db.executemany('INSERT INTO knowledge_tokens VALUES (?,?,?)',
                           [(token,'title',entry_id) for token in sorted(tokens)])
            body=doc['content']
            if digest(body)!=doc['provenance']['content_sha256']:
                raise ValueError('正文哈希校验失败')
            if doc.get('duplicate_of') or doc['category']=='notices':
                continue
            parts=list(chunks(body))
            counts['body_entries']+=bool(parts)
            counts['chunks']+=len(parts)
            db.executemany('INSERT INTO knowledge_chunks VALUES (?,?,?)',
                           [(entry_id,i,text) for i,text in enumerate(parts,1)])
            db.executemany('INSERT INTO knowledge_tokens VALUES (?,?,?)',
                           [(token,'content',entry_id) for token in sorted(tokenize(body))])
        built_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        db.executemany('INSERT INTO meta VALUES (?,?)',[
            ('schema_version','1'),('content_scope',scope),('source_commit',source_commit),
            ('built_at',built_at),('data_license','LicenseRef-Original-Source'),
            ('default_ai_status','confirmed')])
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('知识数据库完整性检查失败')
    except BaseException:
        db.close()
        path.unlink(missing_ok=True)
        raise
    db.close()
    destination=out_dir/'knowledge.sqlite'
    os.replace(path,destination)
    source_dir=ROOT/'sources/data-summary'
    if source_dir.is_dir():
        shutil.copytree(source_dir,out_dir/'data-summary',dirs_exist_ok=True)
        shutil.copy2(ROOT/'docs/data-summary.md',out_dir/'data-summary/SOURCE-NOTICE.md')
    manifest={'schema_version':1,'content_scope':scope,'source_commit':source_commit,
              'built_at':built_at,'counts':counts,'data_license':'LicenseRef-Original-Source',
              'sqlite':{'path':'knowledge.sqlite','sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
                        'bytes':destination.stat().st_size}}
    (out_dir/'knowledge-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


def search(path,text,expanded_names=(),limit=5,include_unconfirmed=False):
    if not text.strip() or limit < 1:
        return []
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        status='' if include_unconfirmed else " AND e.status='confirmed'"
        candidates={}
        for i,name in enumerate([text,*expanded_names]):
            for row in db.execute(
                'SELECT DISTINCT e.id FROM knowledge_names n JOIN knowledge_entries e ON e.id=n.entry_id '
                'WHERE n.name_norm=? AND e.category!=?'+status,(norm(name),'notices')):
                candidates[row['id']]=max(candidates.get(row['id'],0),8000 if i==0 else 5000)
        tokens=sorted(tokenize(text))
        if any(not token.isdigit() for token in tokens):
            tokens=[token for token in tokens if not token.isdigit()]
        if tokens:
            slots=','.join('?' for _ in tokens)
            for row in db.execute(
                "SELECT e.id,SUM(CASE t.kind WHEN 'title' THEN 4 ELSE 1 END) score "
                'FROM knowledge_tokens t JOIN knowledge_entries e ON e.id=t.entry_id '
                f"WHERE t.token IN ({slots}) AND e.category!='notices'"+status+' GROUP BY e.id',tokens):
                candidates[row['id']]=candidates.get(row['id'],0)+row['score']
        results=[]
        # Prefer a canonical record for duplicate pages, preserving any broader title aliases.
        seen=set()
        for entry_id,score in sorted(candidates.items(),key=lambda p:(-p[1],p[0])):
            row=db.execute('SELECT * FROM knowledge_entries WHERE id=?',(entry_id,)).fetchone()
            canonical=row['duplicate_of'] or entry_id
            if canonical in seen:
                continue
            if row['duplicate_of']:
                original=db.execute('SELECT * FROM knowledge_entries WHERE id=?',(canonical,)).fetchone()
                if original is not None and (include_unconfirmed or original['status']=='confirmed'):
                    row=original
                else:
                    canonical=entry_id
            seen.add(canonical)
            doc=json.loads(row['metadata_json'])
            parts=[r['text'] for r in db.execute('SELECT text FROM knowledge_chunks WHERE entry_id=? ORDER BY seq',(row['id'],))]
            if parts:
                best=max(enumerate(parts),key=lambda p:(sum(token in p[1].lower() for token in tokens),-p[0]))[1]
                doc['excerpt']=best
            doc['retrieval']={'kind':'knowledge','score':score,'body_available':bool(parts),
                              'attribution':doc['provenance']['attribution']}
            results.append(doc)
            if len(results)>=limit:
                break
        return results
    finally:
        db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'dist')
    args=parser.parse_args()
    print(json.dumps(build(args.out),ensure_ascii=False,indent=2))
