#!/usr/bin/env python3
"""比较两次构建：表内容（按稳定 ID）、过滤状态、固定问题结果、导出文件。不比 SQLite 文件 SHA。
用法: python3 compare_builds.py <构建A目录> <构建B目录>"""
import sys, os, json, sqlite3, hashlib, importlib.util
A, B = sys.argv[1], sys.argv[2]
TABLES = {  # 表: (稳定键, 比较列；None=全部列)
    'chunks': ('chunk_id', None), 'facts': ('id', None),
    'aliases': ('alias_norm,canonical_norm,source', None),
    'alias_groups': ('alias_norm', None), 'alias_senses': ('alias_norm,sense', None),
}
STATE = {'chunks': ['review_status', 'sensitive', 'publishable', 'license_basis'], 'facts': ['answerable', 'publishable', 'flags'],
         'aliases': ['use_in_chat', 'resolution', 'publishable', 'conflict', 'primary_']}
META_SKIP = {'stats', 'built_from'}   # 含统计/路径，单独比 stats.json
def load(d, t, key):
    c = sqlite3.connect(os.path.join(d, 'build/naikuai-kb.sqlite')); c.row_factory = sqlite3.Row
    out = {}
    for r in c.execute(f'select * from {t}'):
        k = tuple(r[x] for x in key.split(',')); out.setdefault(k, []).append(dict(r))
    return out
rep, ok = {}, True
for t, (key, _) in TABLES.items():
    a, b = load(A, t, key), load(B, t, key)
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    changed = [k for k in set(a) & set(b) if sorted(map(json.dumps, a[k]), key=str) != sorted(map(json.dumps, b[k]), key=str)]
    state_diff = [k for k in set(a) & set(b) if t in STATE and
                  sorted(json.dumps([r[s] for s in STATE[t]]) for r in a[k]) != sorted(json.dumps([r[s] for s in STATE[t]]) for r in b[k])]
    dup = sum(len(v) > 1 for v in a.values())
    rep[t] = {'rows_a': sum(map(len, a.values())), 'rows_b': sum(map(len, b.values())), 'only_a': len(only_a), 'only_b': len(only_b),
              'content_changed': len(changed), 'state_changed': len(state_diff), 'dup_keys': dup, 'sample': [str(x) for x in (only_a + only_b + changed)[:5]]}
    ok &= not (only_a or only_b or changed)
ma = dict(sqlite3.connect(os.path.join(A, 'build/naikuai-kb.sqlite')).execute('select key,value from meta'))
mb = dict(sqlite3.connect(os.path.join(B, 'build/naikuai-kb.sqlite')).execute('select key,value from meta'))
rep['meta'] = {k: [ma.get(k), mb.get(k)] for k in set(ma) | set(mb) if k not in META_SKIP and ma.get(k) != mb.get(k)}
ok &= not rep['meta']
sa, sb = json.load(open(os.path.join(A, 'stats.json'))), json.load(open(os.path.join(B, 'stats.json')))
rep['stats_diff'] = {k: [sa.get(k), sb.get(k)] for k in set(sa) | set(sb) if k not in ('db_size', 'db_size_bytes') and sa.get(k) != sb.get(k)}
ok &= not rep['stats_diff']
# 固定问题：同一份 kb_query.py 分别查两个库
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('kq', os.path.join(HERE, 'kb_query.py')); kq = importlib.util.module_from_spec(spec); spec.loader.exec_module(kq)
qs = [json.loads(l)['q'] for l in open(os.path.join(HERE, 'eval/cases.jsonl'), encoding='utf-8') if l.strip()]
ka, kb_ = kq.KB(os.path.join(A, 'build/naikuai-kb.sqlite')), kq.KB(os.path.join(B, 'build/naikuai-kb.sqlite'))
sig = lambda r: [r['action'], [h['names'] for h in r['terms']], [f['id'] for f in r['facts']], [(c['chunk_id'], hashlib.sha1(c['text'].encode()).hexdigest()[:8]) for c in r['chunks']]]
qd = [q for q in qs if sig(ka.lookup(q)) != sig(kb_.lookup(q))]
rep['fixed_queries'] = {'total': len(qs), 'different': len(qd), 'sample': qd[:5]}
ok &= not qd
# 导出文件（去掉 refine 前后无关的空白差异，逐文件比 SHA）
files = ['sources.csv', 'glossary/aliases.tsv', 'glossary/conflicts.tsv', 'glossary/alias_groups.tsv', 'glossary/alias_senses.tsv',
         'glossary/slang_nkwiki.tsv', 'facts/client.jsonl', 'facts/nkwiki.jsonl', 'docs/chunks.jsonl', 'docs/documents.tsv']
h = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest() if os.path.exists(p) else None
rep['files_different'] = [f for f in files if h(os.path.join(A, f)) != h(os.path.join(B, f))]
ok &= not rep['files_different']
rep['identical'] = ok
print(json.dumps(rep, ensure_ascii=False, indent=1))
