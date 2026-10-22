"""Import the authorized Data Summary text; no external site snapshot is used."""
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from common import ROOT, norm
from knowledge import ATTRIBUTIONS, collect_knowledge_errors, digest, load_knowledge, redact

CATEGORIES = {
    '一': 'basics', '二': 'pve', '三': 'pvp', '四': 'dungeons',
    '五': 'pets', '六': 'fairies', '七': 'systems', '八': 'mounts',
    '九': 'equipment', '十': 'events', '十一': 'enchanting',
}
SUMMARY_URL = 'https://github.com/1777RiKang/naikuai-base-knowledge/blob/main/sources/data-summary/full.md'


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


def make_record(entry_id, title, category, locator, fetched_at, body, tags=(), aliases=()):
    body = body.replace('](images/', '](https://raw.githubusercontent.com/1777RiKang/naikuai-base-knowledge/main/sources/data-summary/images/')
    body = redact(body, allow_author_contact=category == 'notices')
    record = {
        'id': entry_id, 'title': clean_metadata(title), 'category': category,
        'aliases': sorted({clean_metadata(a) for a in aliases if norm(str(a)) != norm(title)}),
        'tags': sorted({clean_metadata(t)[:128] for t in tags if t}),
        'content': body,
        'provenance': {
            'source': 'author', 'attribution': ATTRIBUTIONS['author'],
            'acquired_via': 'local-file', 'url': SUMMARY_URL,
            'locator': locator, 'source_updated_at': None, 'fetched_at': fetched_at,
            'content_sha256': digest(body), 'license': 'LicenseRef-Original-Source',
            'publication': 'fulltext', 'origin_rule': 'direct-summary',
        },
        'review': {'status': 'draft', 'reviewer': None, 'reviewed_at': None},
        'imported': True, 'credit_eligible': False,
    }
    return record


def collect_snapshot(summary_path):
    summary_path = Path(summary_path)
    summary = summary_path.read_text(encoding='utf-8-sig')
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows = []
    for part in split_summary(summary):
        record = make_record(part['id'], part['title'],
            category_for(part['title'], part['section']), part['locator'], fetched_at,
            part['body'], [part['section'], '数据摘要'])
        rows.append(record)
    receipt = {
        'source': 'authorized-author-summary', 'fetched_at': fetched_at,
        'external_content_publication': 'fulltext',
        'summary_attribution': ATTRIBUTIONS['author'],
        'summary_version': '8.6.26.10',
        'summary_sha256': hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        'authorization': {'confirmed_by': 'maintainer', 'confirmed_on': '2026-10-22',
                          'public_fulltext_requested_on': '2026-10-22',
                          'license': 'LicenseRef-Original-Source'},
        'counts': {'knowledge_entries': len(rows),
                   'categories': dict(sorted(Counter(r['category'] for r in rows).items())),
                   'sources': {'author': len(rows)}},
    }
    return rows, receipt


def write_snapshot(records, receipt, catalog_dir):
    catalog_dir = Path(catalog_dir)
    previous = {d['id']: (p,d) for p,d in load_knowledge(catalog_dir)}
    candidates = []
    for record in records:
        old = previous.get(record['id'])
        if old and old[1]['provenance']['content_sha256'] == record['provenance']['content_sha256']:
            record['review'] = copy.deepcopy(old[1]['review'])
            record['provenance']['fetched_at'] = old[1]['provenance']['fetched_at']
        target = catalog_dir / record['category'] / (record['id'].split('.',1)[1]+'.json')
        candidates.append((target, record))
    # Missing author sections stay deprecated for traceability. Site records are removed.
    for entry_id,(path,doc) in previous.items():
        if entry_id not in {record['id'] for record in records} and doc.get('content') and doc['provenance']['source'] == 'author':
            doc = copy.deepcopy(doc)
            doc['review'] = {'status':'deprecated','reviewer':None,'reviewed_at':None}
            doc.pop('duplicate_of',None)
            candidates.append((path,doc))
    errors = collect_knowledge_errors(candidates)
    if errors:
        raise ValueError('\n'.join(errors))
    expected = {path for path,_ in candidates}
    # Only remove catalog files discovered under the explicitly supplied catalog root.
    for path,_ in previous.values():
        path.resolve().relative_to(catalog_dir.resolve())
        if path not in expected:
            path.unlink()
    for path,record in candidates:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, default=ROOT/'sources/data-summary/full.md')
    args = parser.parse_args()
    records,receipt = collect_snapshot(args.summary)
    write_snapshot(records,receipt,ROOT/'knowledge')
    (ROOT/'sources/import-manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    from catalog_docs import generate
    generate()
    print(json.dumps(receipt['counts'],ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
