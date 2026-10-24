#!/usr/bin/env python3
"""第二轮修正（幂等）：在 build_unified.py 的产物上执行。
- 别名：区分 single / variant / group / ambiguous；补 publishable；建组合实体表 alias_groups、多义表 alias_senses
- 文档块：加 review_status（active / candidate / legacy）与 license_basis；黑话百科块→candidate，旧版 BOSS 速查→legacy
- 事实：补 publishable；客户端停用宝石的同名 nkwiki 记录也设为不可回答
- 元数据：数据摘要有效期按“快照日期 + 复核日期”记录，不推断失效
- 统一统计口径，重写 stats.json
用法: python3 refine.py [输出目录]"""
import sys, os, csv, json, sqlite3, collections, re, unicodedata
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
def norm(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[\s·・\-_/|\\,，。、:：;；"“”\'‘’()（）\[\]【】<>《》!！?？*#`~]+', '', s)

# 人工决定（2026-10-24 用户评审）
AMBIGUOUS = {  # 别名 → (义项, 消歧关键词)。语境不足时先反问
    '碎片': [('赐福碎片', ['赐福']), ('装备碎片', ['装备']), ('传说碎片', ['传说'])],
}
LEGACY_DOCS = {'botkb:13-副本BOSS速查': '8.6.26.08 旧版；核对 arena.txt / v2 前只作参考，不作为当前人数、装等、时限的确定答案'}
CANDIDATE_TITLE = re.compile(r'黑话')
SUMMARY_META = {
    'summary_version': '8.6.26.10',
    'summary_validity_text': '原文：“文件生效期为1个月”，未写明起算日期',
    'summary_snapshot_date': '2026-10-21',   # v10-full.md 快照文件日期（线上库同日构建），是能证明的最早日期
    'summary_review_due': '2026-11-21',      # 复核提醒日，不是失效日
    'summary_after_due': 'needs_review',     # 到期只提示“待复核”，不自动判定失效
}

# ---------- 1. 别名 ----------
ap = os.path.join(OUT, 'glossary/aliases.tsv')
rows = list(csv.DictReader(open(ap, encoding='utf-8'), delimiter='\t'))
by = collections.defaultdict(list)
for r in rows: by[norm(r['alias'])].append(r)
groups, senses = [], []
for an, rs in by.items():
    canon = {norm(r['canonical']) for r in rs}
    alias = rs[0]['alias']
    if alias in AMBIGUOUS:
        res = 'ambiguous'
        for r in rs: r['use_in_chat'] = 'yes' if r.get('primary') == 'primary' else 'no'
        for s, kw in AMBIGUOUS[alias]: senses.append((an, alias, s, '|'.join(kw)))
    elif len(canon) == 1: res = 'single'
    elif len({r['source'] for r in rs}) == 1 and rs[0]['source'] in ('nkwiki_patch', 'v2_client'):
        res = 'group'
        for r in rs: r['use_in_chat'] = 'yes'
        groups.append((an, alias, json.dumps(sorted({r['canonical'] for r in rs}), ensure_ascii=False), rs[0]['kind'], rs[0]['source']))
    else:
        res = 'variant'   # 写法差异：只启用最高优先来源
    for r in rs:
        r['resolution'] = res
        r['publishable'] = 'yes' if r['source'] in ('repo_term', 'manual_review') else 'no'
cols = ['alias', 'canonical', 'kind', 'source', 'status', 'ref', 'note', 'conflict', 'primary', 'use_in_chat', 'resolution', 'publishable']
with open(ap, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, cols, delimiter='\t', lineterminator='\n', extrasaction='ignore'); w.writeheader(); w.writerows(rows)
with open(os.path.join(OUT, 'glossary/conflicts.tsv'), 'w', encoding='utf-8') as f:
    f.write('alias\tresolution\tcandidates\n')
    for an, rs in by.items():
        if rs[0]['resolution'] in ('variant', 'group', 'ambiguous'):
            f.write(f"{rs[0]['alias']}\t{rs[0]['resolution']}\t" + ' | '.join(sorted({f"{r['canonical']}[{r['source']}]" for r in rs})) + '\n')

# ---------- 2. SQLite ----------
db = sqlite3.connect(os.path.join(OUT, 'build/naikuai-kb.sqlite'))
def addcol(t, c, decl):
    if c not in [x[1] for x in db.execute(f'pragma table_info({t})')]: db.execute(f'alter table {t} add column {c} {decl}')
db.execute('delete from aliases')
for c_, d in [('resolution', 'TEXT'), ('publishable', 'INT')]: addcol('aliases', c_, d)
db.executemany('insert into aliases(alias,alias_norm,canonical,canonical_norm,kind,source,status,ref,note,conflict,primary_,use_in_chat,resolution,publishable) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
    [(r['alias'], norm(r['alias']), r['canonical'], norm(r['canonical']), r['kind'], r['source'], r['status'], r['ref'], r['note'],
      int(r['conflict'] == 'conflict'), int(r['primary'] == 'primary'), int(r['use_in_chat'] == 'yes'), r['resolution'], int(r['publishable'] == 'yes')) for r in rows])
db.executescript('''drop table if exists alias_groups; drop table if exists alias_senses;
create table alias_groups(alias_norm TEXT PRIMARY KEY, alias TEXT, members TEXT, kind TEXT, source TEXT);
create table alias_senses(alias_norm TEXT, alias TEXT, sense TEXT, keywords TEXT);''')
db.executemany('insert into alias_groups values(?,?,?,?,?)', groups)
db.executemany('insert into alias_senses values(?,?,?,?)', senses)
with open(os.path.join(OUT, 'glossary/alias_groups.tsv'), 'w', encoding='utf-8') as f:
    f.write('alias\tmembers\tkind\tsource\n'); [f.write(f'{g[1]}\t{"|".join(json.loads(g[2]))}\t{g[3]}\t{g[4]}\n') for g in groups]
with open(os.path.join(OUT, 'glossary/alias_senses.tsv'), 'w', encoding='utf-8') as f:
    f.write('alias\tsense\tkeywords\n'); [f.write(f'{x[1]}\t{x[2]}\t{x[3]}\n') for x in senses]

for c_, d in [('review_status', "TEXT DEFAULT 'active'"), ('status_note', "TEXT DEFAULT ''"), ('license_basis', "TEXT DEFAULT ''")]: addcol('chunks', c_, d)
db.execute("update chunks set review_status='active', status_note=''")
db.execute("update chunks set review_status='candidate', status_note='黑话百科正文，对应 slang_nkwiki.tsv，审核通过前不检索' where source='nkwiki/encyclopedia' and title like '%黑话%'")
for doc, note in LEGACY_DOCS.items(): db.execute("update chunks set review_status='legacy', status_note=? where doc_id=?", (note, doc))
db.execute("""update chunks set license_basis=case
  when source='author/乱走丶沙城' then '作者授权公开（授权凭据需另存档；须保留免责声明、不得删改）'
  when source like 'nkwiki/%' then '无再分发许可'
  when publishable=1 then '待确认（社区整理，按仓库 CC BY-NC-SA 口径）'
  else '客户端派生，无公开许可' end""")

addcol('facts', 'publishable', 'INT DEFAULT 0')
db.execute('update facts set publishable=0')   # 客户端派生与 nkwiki 结构化均无公开许可
disabled = [n for (n,) in db.execute("select name_norm from facts where kind='gem' and answerable=0")]
n_wiki_disabled = 0
for n in disabled:
    cur = db.execute("""update facts set answerable=0, flags=json_insert(flags,'$[#]','client_disabled')
                        where kind in ('wiki_gem','wiki_gem_attr') and name_norm=? and answerable=1""", (n,))
    n_wiki_disabled += cur.rowcount
db.executemany('insert or replace into meta values(?,?)', list(SUMMARY_META.items()))
db.commit()

# ---------- 3. 同步 chunks.jsonl / facts jsonl ----------
st = {r[0]: r[1:] for r in db.execute('select chunk_id,review_status,status_note,license_basis from chunks')}
cp = os.path.join(OUT, 'docs/chunks.jsonl'); out = []
for line in open(cp, encoding='utf-8'):
    d = json.loads(line); d['review_status'], d['status_note'], d['license_basis'] = st[d['chunk_id']]; out.append(d)
with open(cp, 'w', encoding='utf-8') as f: f.writelines(json.dumps(d, ensure_ascii=False) + '\n' for d in out)
fa = {r[0]: (r[1], json.loads(r[2])) for r in db.execute('select id,answerable,flags from facts')}
for fn in ['client', 'nkwiki']:
    p = os.path.join(OUT, f'facts/{fn}.jsonl'); buf = []
    for line in open(p, encoding='utf-8'):
        d = json.loads(line); a, fl = fa.get(d['id'], (int(d['answerable']), d['flags']))
        d['answerable'] = bool(a); d['flags'] = fl; d['publishable'] = False; buf.append(d)
    with open(p, 'w', encoding='utf-8') as f: f.writelines(json.dumps(d, ensure_ascii=False) + '\n' for d in buf)

# ---------- 4. 统一统计口径 ----------
q = lambda s: db.execute(s).fetchone()
S = json.load(open(os.path.join(OUT, 'stats.json'), encoding='utf-8'))
S['definitions'] = {
    'chunk_body_chars': '块正文字符数（不含【标题/小节】前缀），含 Markdown 符号',
    'chunk_text_chars': '入库文本字符数（含前缀），即检索与送给模型的文本',
    'alias_rows': 'aliases.tsv 行数（同一别名不同来源各算一行）',
    'alias_forms_enabled': '启用行里不同别名写法（按 norm 去重）的数量',
    'db_size': '解压后 SQLite 文件大小；ZIP 大小另计',
}
S['chunks'] = q('select count(*) from chunks')[0]
S['chunk_body_chars'], S['chunk_text_chars'] = q('select sum(char_count), sum(length(text)) from chunks')
S['chunks_by_status'] = dict(db.execute('select review_status,count(*) from chunks group by 1').fetchall())
S['chunks_retrievable_default'] = q("select count(*) from chunks where review_status='active' and sensitive=0")[0]
S['chunks_publishable_tag'] = q('select count(*) from chunks where publishable=1')[0]
S['chunks_publishable_tag_not_sensitive'] = q('select count(*) from chunks where publishable=1 and sensitive=0')[0]
S['chunks_publishable_tag_not_sensitive_active'] = q("select count(*) from chunks where publishable=1 and sensitive=0 and review_status='active'")[0]
S['facts_total'] = q('select count(*) from facts')[0]
S['facts_not_answerable'] = q('select count(*) from facts where answerable=0')[0]
S['facts_wiki_marked_by_client_disabled'] = n_wiki_disabled
S['alias_rows'] = len(rows)
S['alias_rows_enabled'] = sum(r['use_in_chat'] == 'yes' for r in rows)
S['alias_forms_enabled'] = len({norm(r['alias']) for r in rows if r['use_in_chat'] == 'yes'})
S['alias_resolution'] = dict(collections.Counter(r['resolution'] for r in rows))
S['alias_groups'] = len(groups); S['alias_ambiguous'] = len({s[0] for s in senses})
S.pop('alias_conflicts_curated', None)
db.execute('insert or replace into meta values(?,?)', ('stats', json.dumps(S, ensure_ascii=False)))
db.commit(); db.execute('VACUUM'); db.close()
sz = os.path.getsize(os.path.join(OUT, 'build/naikuai-kb.sqlite'))
S['db_size_bytes'] = sz; S['db_size'] = f'{sz / 1e6:.2f} MB（{sz / 2**20:.2f} MiB）'
json.dump(S, open(os.path.join(OUT, 'stats.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps({k: S[k] for k in ['chunks', 'chunk_body_chars', 'chunk_text_chars', 'chunks_by_status', 'chunks_retrievable_default',
    'chunks_publishable_tag', 'chunks_publishable_tag_not_sensitive', 'chunks_publishable_tag_not_sensitive_active', 'facts_total',
    'facts_not_answerable', 'facts_wiki_marked_by_client_disabled', 'alias_rows', 'alias_rows_enabled', 'alias_forms_enabled',
    'alias_resolution', 'alias_groups', 'alias_ambiguous', 'db_size']}, ensure_ascii=False, indent=1))
