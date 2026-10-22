"""Import supplied offline snapshots. Publish metadata only; keep bodies local."""
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from common import ROOT, norm
from knowledge import collect_knowledge_errors, digest, load_knowledge, redact

CATEGORIES = {
    '一': 'basics', '二': 'pve', '三': 'pvp', '四': 'dungeons',
    '五': 'pets', '六': 'fairies', '七': 'systems', '八': 'mounts',
    '九': 'equipment', '十': 'events', '十一': 'enchanting',
}
KINDS = {
    'equipment': ('equipment', 'name'), 'gems': ('gems', 'name'),
    'gem_attributes': ('gem-attributes', 'name'), 'fairies': ('fairies', 'name'),
    'encyclopedia': ('encyclopedia', 'title'),
}


def clean_metadata(text):
    return re.sub(r'\s+', ' ', redact(str(text))).strip()[:256] or '未命名'


def category_for(title, section):
    if '免责声明' in section or '使用说明' in title:
        return 'notices'
    if re.search('合成|制作|升阶材料', title):
        return 'crafting'
    if re.search('成就任务|主线|支线', title):
        return 'quests'
    match = re.search(r'第([一二三四五六七八九十]+)章', section)
    if match:
        return CATEGORIES.get(match[1], 'basics')
    if '概率' in title:
        return 'basics'
    return 'worlds'


def split_summary(text):
    """Own-section text only: nested sections are independent, without repetition."""
    headings = list(re.finditer(r'^(#{1,6})\s+(.+?)\s*$', text, re.M))
    stack = []
    for i, match in enumerate(headings):
        level, title = len(match[1]), match[2].strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        body = text[match.end():headings[i+1].start() if i+1 < len(headings) else len(text)].strip()
        if not body:
            continue
        path = ' / '.join(t for _, t in stack)
        stable = hashlib.sha256(path.encode('utf-8')).hexdigest()[:16]
        yield {'id': 'knowledge.author-summary-' + stable, 'title': title,
               'section': stack[0][1], 'locator': path, 'body': body}


def fingerprint(text):
    text = re.sub(r'^#{1,6}\s+.*$', '', text, flags=re.M)
    text = re.sub(r'(?m)^\s*\|?[\s|:\-]{3,}\|?\s*$', '', text)
    return digest(re.sub(r'\s+', '', text))


def is_summary_article(row, summary_compact):
    section = row.get('categoryName') or ''
    body = row.get('content') or ''
    if section == '免责声明' or re.match(r'^第.+章', section):
        return True, 'summary-chapter'
    if re.search(r'^#\s+第.+章', body, re.M):
        return True, 'summary-heading'
    compact = re.sub(r'[\s#*|\-]+', '', body)
    if len(compact) >= 120:
        samples = [compact[i:i+100] for i in (0, len(compact)//2, max(0, len(compact)-100))]
        if sum(sample in summary_compact for sample in samples) >= 2:
            return True, 'summary-text-match'
    return False, 'site-record'


def make_record(entry_id, title, category, source, via, locator, fetched_at,
                updated_at, body, tags=(), aliases=(), rule='direct-source'):
    record = {
        'id': entry_id, 'title': clean_metadata(title), 'category': category,
        'aliases': sorted({clean_metadata(a) for a in aliases if norm(str(a)) != norm(title)}),
        'tags': sorted({clean_metadata(t)[:128] for t in tags if t}),
        'provenance': {
            'source': source, 'attribution': '作者' if source == 'author' else 'nkwiki',
            'acquired_via': via, 'url': 'https://nkwiki.cc/',
            'locator': locator, 'source_updated_at': updated_at,
            'fetched_at': fetched_at, 'content_sha256': digest(redact(body)),
            'license': 'LicenseRef-Original-Source', 'publication': 'metadata-only',
            'origin_rule': rule,
        },
        'review': {'status': 'draft', 'reviewer': None, 'reviewed_at': None},
        'imported': True, 'credit_eligible': False,
    }
    return record, redact(body)


def load_aliases(database):
    """Use only the base wiki database, never patched community records."""
    if not database.exists():
        return {}
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        names = {}
        for name, alias in db.execute('SELECT e.name,a.alias FROM entities e JOIN aliases a ON a.entity_id=e.id'):
            names.setdefault(norm(name), set()).add(alias)
        return names
    finally:
        db.close()


def collect_snapshot(wiki_dir, summary_path):
    wiki_dir, summary_path = Path(wiki_dir), Path(summary_path)
    manifest = json.loads((wiki_dir/'raw/manifest.json').read_text(encoding='utf-8-sig'))
    for filename, expected in manifest['files'].items():
        raw = (wiki_dir/'raw'/filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected['sha256']:
            raise ValueError(f'原始快照校验失败：{filename}')
    fetched_at = manifest['fetchedAt']
    summary = summary_path.read_text(encoding='utf-8-sig')
    compact_summary = re.sub(r'[\s#*|\-]+', '', summary)
    rows, bodies, fingerprints = [], {}, {}
    aliases = load_aliases(wiki_dir/'wiki-kb.sqlite')

    def append(record, body):
        fp = fingerprint(body)
        if len(body) > 30 and fp in fingerprints:
            record['duplicate_of'] = fingerprints[fp]
        elif len(body) > 30:
            fingerprints[fp] = record['id']
        rows.append(record)
        bodies[record['id']] = {'id': record['id'], 'content': body,
                                'content_sha256': record['provenance']['content_sha256']}

    for part in split_summary(summary):
        record, body = make_record(part['id'], part['title'],
            category_for(part['title'], part['section']), 'author', 'local-file',
            part['locator'], fetched_at, None, part['body'], [part['section'], '数据摘要'],
            rule='direct-summary')
        append(record, body)

    articles = json.loads((wiki_dir/'raw/articles.json').read_text(encoding='utf-8-sig'))
    for row in sorted(articles, key=lambda r: r['id']):
        if row.get('isDeleted') or row.get('isPublished') is False:
            continue
        author, rule = is_summary_article(row, compact_summary)
        section = row.get('categoryName') or ''
        record, body = make_record('knowledge.nkwiki-article-' + str(row['id']),
            row['title'], category_for(row['title'], section),
            'author' if author else 'nkwiki', 'nkwiki', 'article:' + str(row['id']),
            fetched_at, row.get('updatedAt'), row.get('content') or '', [section],
            rule=rule)
        append(record, body)

    details = json.loads((wiki_dir/'raw/encyclopedia_detail.json').read_text(encoding='utf-8-sig'))
    for kind, (category, name_key) in KINDS.items():
        dataset = json.loads((wiki_dir/'raw'/f'{kind}.json').read_text(encoding='utf-8-sig'))
        for row in sorted(dataset, key=lambda r: r['id']):
            if row.get('isDeleted'):
                continue
            detailed = details.get(str(row['id']), row) if kind == 'encyclopedia' else row
            # Keep source fields locally; discard site author/account and tracking fields.
            safe = {k: v for k, v in detailed.items() if k not in {
                'authorId', 'authorName', 'viewCount', 'likeCount', 'commentCount',
                'collectionCount', 'deletedAt'}}
            body = json.dumps(safe, ensure_ascii=False, indent=2)
            name = row[name_key]
            record, body = make_record(
                'knowledge.nkwiki-' + kind.replace('_', '-') + '-' + str(row['id']),
                name, category, 'nkwiki', 'nkwiki', kind + ':' + str(row['id']),
                fetched_at, row.get('updatedAt'), body, [row.get('categoryName') or category],
                aliases.get(norm(name), ()))
            append(record, body)
    receipt = {
        'source': 'offline-user-supplied-snapshot', 'fetched_at': fetched_at,
        'external_content_publication': 'metadata-only',
        'summary_attribution': '作者', 'site_attribution': 'nkwiki',
        'source_files': {k: {'sha256': v['sha256'], 'bytes': v['bytes']}
                         for k,v in manifest['files'].items()},
        'summary_sha256': hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        'wiki_database_sha256': hashlib.sha256((wiki_dir/'wiki-kb.sqlite').read_bytes()).hexdigest(),
        'counts': {'knowledge_catalog': len(rows), 'categories': dict(sorted(Counter(r['category'] for r in rows).items())),
                   'sources': dict(Counter(r['provenance']['source'] for r in rows)),
                   'exact_duplicates': sum('duplicate_of' in r for r in rows)},
        'source_snapshot_counts': manifest['counts'],
    }
    return rows, bodies, receipt


def write_snapshot(records, bodies, receipt, catalog_dir, private_dir):
    catalog_dir, private_dir = Path(catalog_dir), Path(private_dir)
    try:
        private_dir.resolve().relative_to((ROOT/'.local').resolve())
    except ValueError:
        raise ValueError('本地全文输出必须位于本仓库被忽略的 .local/ 目录') from None
    previous = {d['id']: (p,d) for p,d in load_knowledge(catalog_dir)}
    present = set()
    candidates = []
    for record in records:
        present.add(record['id'])
        old = previous.get(record['id'])
        if old and old[1]['provenance']['content_sha256'] == record['provenance']['content_sha256']:
            record['review'] = copy.deepcopy(old[1]['review'])
        target = catalog_dir/record['category']/(record['id'].split('.',1)[1]+'.json')
        candidates.append((target, record))
    for entry_id,(path,doc) in previous.items():
        if entry_id not in present:
            doc = copy.deepcopy(doc)
            doc['review'] = {'status':'deprecated','reviewer':None,'reviewed_at':None}
            # A retired copy must not retain a link to a newly changed source body.
            doc.pop('duplicate_of', None)
            candidates.append((path,doc))
    errors = collect_knowledge_errors(candidates)
    if errors:
        raise ValueError('\n'.join(errors))
    for path,record in candidates:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        old = previous.get(record['id'])
        if old and old[0] != path:
            old[0].unlink()
    private_dir.mkdir(parents=True,exist_ok=True)
    with (private_dir/'bodies.jsonl').open('w',encoding='utf-8') as output:
        for key in sorted(bodies):
            output.write(json.dumps(bodies[key],ensure_ascii=False)+'\n')
    (private_dir/'import-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return candidates


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nkwiki-dir',type=Path,required=True)
    parser.add_argument('--summary',type=Path,required=True)
    args=parser.parse_args()
    records,bodies,receipt=collect_snapshot(args.nkwiki_dir,args.summary)
    write_snapshot(records,bodies,receipt,ROOT/'knowledge',ROOT/'.local')
    (ROOT/'sources').mkdir(exist_ok=True)
    (ROOT/'sources/import-manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    from catalog_docs import generate
    generate()
    print(json.dumps(receipt['counts'],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
