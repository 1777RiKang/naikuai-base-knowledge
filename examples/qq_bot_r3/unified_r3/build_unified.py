#!/usr/bin/env python3
"""把 naikuai-kb-full 全量包整理成统一知识库：sources / glossary / facts / chunks / sqlite。
用法: python3 build_unified.py <全量包目录> <输出目录>"""
import sys, os, re, json, csv, hashlib, sqlite3, glob, collections, unicodedata
SRC, OUT = sys.argv[1], sys.argv[2]
P = lambda *a: os.path.join(SRC, *a)
for d in ['glossary', 'facts', 'docs', 'build']: os.makedirs(os.path.join(OUT, d), exist_ok=True)
stats = collections.OrderedDict()

def norm(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[\s·・\-_/|\\,，。、:：;；"“”\'‘’()（）\[\]【】<>《》!！?？*#`~]+', '', s)

# ---------------- 1. 来源登记 ----------------
man = json.load(open(P('MANIFEST.json')))
files = man['files'] if 'files' in man else []
if not files:
    for root, _, fs in os.walk(SRC):
        for f in fs:
            p = os.path.join(root, f); files.append({'path': os.path.relpath(p, SRC), 'bytes': os.path.getsize(p),
                'sha256': hashlib.sha256(open(p, 'rb').read()).hexdigest()})
by_sha = collections.defaultdict(list)
for f in files: by_sha[f['sha256']].append(f['path'])
dup_of = {}
PRI = ['03-curated/bot-kb/', '01-nkwiki/', '05-v2/', '04-upstream/docs-02-gamedata/', '03-curated/naikuai-base-knowledge/']
def rank(p):
    for i, x in enumerate(PRI):
        if p.startswith(x): return i
    return 99
for sha, ps in by_sha.items():
    if len(ps) > 1:
        ps = sorted(ps, key=lambda p: (rank(p), len(p)))
        for p in ps[1:]: dup_of[p] = ps[0]

RULES = [  # (前缀, 类型, tier, 版本, 处理, 可公开, 说明)
 ('00-START-HERE.md', '说明', '-', '-', '备查', '否', '包说明'),
 ('MANIFEST.json', '校验', '-', '-', '备查', '否', ''), ('SHA256SUMS.txt', '校验', '-', '-', '备查', '否', ''),
 ('01-nkwiki/raw/articles.json', 'nkwiki 攻略原文', 'L2', '抓取 2026-10-22', '采用→docs（与数据摘要重复部分去重）', '否', '未确认再分发许可；约 9 成是《数据摘要》转载'),
 ('01-nkwiki/raw/encyclopedia', 'nkwiki 百科', 'L2', '抓取 2026-10-22', '采用→docs + 黑话候选', '否', '黑话 4 篇抽成 glossary/slang_nkwiki.tsv（待审）'),
 ('01-nkwiki/raw/', 'nkwiki 结构化', 'L2', '抓取 2026-10-22', '采用→facts', '否', '装备/宝石/宝石属性/精灵'),
 ('01-nkwiki/text/', 'nkwiki 渲染副本', 'L2', '抓取 2026-10-22', '排除', '否', 'raw/ 的渲染副本，冲突以 raw 为准'),
 ('01-nkwiki/db/wiki-kb-patched.sqlite', 'nkwiki 补丁库', 'L2', '2026-10-22', '采用→glossary（副本/BOSS 别名）', '否', ''),
 ('01-nkwiki/db/', 'nkwiki 原始库/报告', 'L2', '', '备查', '否', ''),
 ('01-nkwiki/provenance/', '抓取证据', '-', '', '备查', '否', ''),
 ('01-nkwiki/nkwiki-original-archive.7z', '原始压缩包', '-', '', '备查', '否', ''),
 ('02-manual-terms/review_final.csv', '公屏人工标注', 'L4', '2026-10-22', '采用→glossary', '是（证据已脱敏前不要公开证据列）', '51 行'),
 ('02-manual-terms/', '标注过程文件', '-', '', '备查', '否', '候选/证据/影子报告，含群聊原文，勿公开'),
 ('03-curated/naikuai-base-knowledge/terms/', '社区词条', 'L4', 'v0.3.0', '采用→glossary（最高优先，confirmed）', '是', ''),
 ('03-curated/naikuai-base-knowledge/knowledge/', '社区知识（数据摘要切片）', 'L4', '8.6.26.10', '去重：与数据摘要 v10 重复', '是', 'draft 状态；98% 内容已在 v10 全文中'),
 ('03-curated/naikuai-base-knowledge/', '社区仓库其它', '-', 'v0.3.0', '备查', '是', ''),
 ('03-curated/bot-kb/01-玩家知识库/0', '玩家库·数据摘要', 'L4', '8.6.26.09', '排除（被 8.6.26.10 取代）', '是', '00 为目录；01–09 与 v10 同章节，旧一版'),
 ('03-curated/bot-kb/01-玩家知识库/10', '玩家库·数据摘要', 'L4', '8.6.26.09', '排除（被 8.6.26.10 取代）', '是', ''),
 ('03-curated/bot-kb/01-玩家知识库/11', '玩家库·数据摘要', 'L4', '8.6.26.09', '排除（被 8.6.26.10 取代）', '是', ''),
 ('03-curated/bot-kb/01-玩家知识库/', '玩家库·专题', '见 frontmatter', '见 frontmatter', '采用→docs', '部分（12/13 含客户端数据，否）', '12–18'),
 ('03-curated/bot-kb/02-结构化数据/', '客户端派生结构化', 'L1', '8.6.26.09', '采用→facts', '否', '客户端表派生，不进公开仓库'),
 ('03-curated/bot-kb/', '运维文档', '-', '', '禁喂', '否', '审计/技术索引/导入指南/禁喂清单'),
 ('03-curated/flat-export/', 'bot-kb 扁平副本', '-', '', '排除（重复）', '否', '与 bot-kb 完全重复'),
 ('04-upstream/game-config/arena.txt', '客户端 arena 表', 'L1', '', '备查（BOSS 限时/人数/装等的权威核对表）', '否', ''),
 ('04-upstream/docs-02-gamedata/数据摘要', '数据摘要旧版', 'L4', '旧版', '排除（历史）', '是', ''),
 ('04-upstream/docs-02-gamedata/', '逆向/技术文档', '-', '', '禁喂', '否', '反编译、协议、表结构'),
 ('04-upstream/奶块数据/', '旧版玩家资料', 'L4', '8.6.26.08 / 8.6.5.19', '排除（历史/重复）', '否', ''),
 ('04-upstream/物品ID映射/', '客户端解码表', 'L1', '', '排除（已进 v2）', '否', ''),
 ('04-upstream/data-dicts/', '客户端原始表', 'L1', '', '排除（只用于重建）', '否', ''),
 ('05-v2/game-kb-v2.sqlite', 'v2 结构化库', 'L1', '8.6.26.09', '备查（facts 用 bot-kb 同源 jsonl）', '否', ''),
 ('05-v2/', 'v2 构建/报告', '-', '', '备查', '否', ''),
 ('06-production/evidence/v10-full.md', '数据摘要全文', 'L4', '8.6.26.10', '采用→docs（主版本）', '是（作者授权，须保留声明完整）', '最新版'),
 ('06-production/', '线上快照', '-', '2026-10-21/22', '备查（勿覆盖 v2）', '否', ''),
]
def classify(p):
    for r in RULES:
        if p.startswith(r[0]): return r
    return ('', '未分类', '', '', '备查', '否', '')
with open(os.path.join(OUT, 'sources.csv'), 'w', newline='', encoding='utf-8-sig') as fo:
    w = csv.writer(fo); w.writerow(['路径', '字节', '类型', 'tier', '版本', '处理', '可公开', '重复于', '说明'])
    cnt = collections.Counter()
    for f in sorted(files, key=lambda x: x['path']):
        r = classify(f['path']); act = r[4]; note = r[6]
        if f['bytes'] == 0: act, note = '排除（空文件）', '0 字节'
        if f['path'] in dup_of and not act.startswith('排除'): act = '排除（重复）'
        cnt[act.split('（')[0].split('→')[0]] += 1
        w.writerow([f['path'], f['bytes'], r[1], r[2], r[3], act, r[5], dup_of.get(f['path'], ''), note])
stats['files'] = len(files); stats['exact_dup_files'] = len(dup_of); stats['empty_files'] = sum(1 for f in files if f['bytes'] == 0)
stats['file_actions'] = dict(cnt)

# ---------------- 2. 术语表 ----------------
import yaml
G = []  # alias, canonical, kind, source, status, ref, note
for y in sorted(glob.glob(P('03-curated/naikuai-base-knowledge/terms/*/*.yaml'))):
    d = yaml.safe_load(open(y, encoding='utf-8'))
    st = (d.get('review') or {}).get('status', 'draft'); ref = os.path.relpath(y, P('03-curated/naikuai-base-knowledge'))
    defin = '；'.join(s.get('definition', '') for s in d.get('senses') or [])
    for a in [d['term']] + (d.get('aliases') or []):
        G.append([a, d['canonical'], d.get('kind', ''), 'repo_term', st, ref, defin])
with open(P('02-manual-terms/review_final.csv'), encoding='utf-8-sig') as fi:
    for r in csv.DictReader(fi):
        if r['人工判定'] not in ('保留', '自动保留'): continue
        G.append([r['术语'], r['规范写法'], r['类型'], 'manual_review', 'reviewed' if r['人工判定'] == '保留' else 'auto', 'review_final.csv', r['人工备注'] or r['备注']])
c = sqlite3.connect(P('01-nkwiki/db/wiki-kb-patched.sqlite'))
for name, alias, kind in c.execute('select e.name,a.alias,e.kind from aliases a join entities e on e.id=a.entity_id'):
    if alias != name: G.append([alias, name, kind, 'nkwiki_patch', 'nkwiki', 'wiki-kb-patched.sqlite', ''])
c.close()
c = sqlite3.connect(P('05-v2/game-kb-v2.sqlite')); v2names = collections.defaultdict(set)
internal = 0
for alias, eid, name, kind in c.execute('select a.alias,a.entity_id,e.name,e.kind from aliases a join entities e using(entity_id)'):
    v2names[norm(name)].add(eid)
    if alias == name: continue
    if re.fullmatch(r'[\x00-\x7f]+', alias): internal += 1; continue   # 内部英文名不给聊天用
    G.append([alias, name, kind, 'v2_client', 'L1', eid, ''])
c.close(); stats['v2_internal_aliases_skipped'] = internal
# nkwiki 黑话百科 → 定义表（待审）
slang = []
enc = json.load(open(P('01-nkwiki/raw/encyclopedia_detail.json'), encoding='utf-8'))
for k, v in enc.items():
    if '黑话' not in v['title']: continue
    for line in v['content'].splitlines():
        cells = [x.strip() for x in line.strip().strip('|').split('|')]
        if len(cells) >= 2 and cells[0] and not re.fullmatch(r'[:\-\s]+', cells[0]) and cells[0] != '黑话':
            for t in re.split(r'[/／、]', cells[0]):
                if t.strip(): slang.append([t.strip(), cells[0], cells[1], v['title'], 'nkwiki_draft'])
with open(os.path.join(OUT, 'glossary/slang_nkwiki.tsv'), 'w', encoding='utf-8') as fo:
    fo.write('term\tgroup\tdefinition\tsource\tstatus\n'); [fo.write('\t'.join(x) + '\n') for x in slang]
stats['slang_nkwiki'] = len(slang)
# 冲突检测：同一别名 → 多个不同规范名（v2 内部同名多 ID 不算冲突）
SRC_RANK = {'repo_term': 0, 'manual_review': 1, 'nkwiki_patch': 2, 'v2_client': 3}
by_alias = collections.defaultdict(list)
for g in G: by_alias[norm(g[0])].append(g)
rows, conflicts = [], []
seen = set()
for an, gs in by_alias.items():
    if not an: continue
    gs.sort(key=lambda g: SRC_RANK[g[3]])
    canon = {norm(g[1]) for g in gs}
    conf = len(canon) > 1
    if conf: conflicts.append((gs[0][0], ' | '.join(sorted({f'{g[1]}[{g[3]}]' for g in gs}))))
    for g in gs:
        key = (an, norm(g[1]), g[3])
        if key in seen: continue
        seen.add(key)
        chat = 'yes' if g[4] in ('confirmed', 'reviewed', 'L1', 'nkwiki') and not (conf and g[3] != gs[0][3]) else 'no'
        rows.append(g + [('conflict' if conf else ''), ('primary' if g is gs[0] else ''), chat])
with open(os.path.join(OUT, 'glossary/aliases.tsv'), 'w', encoding='utf-8') as fo:
    fo.write('alias\tcanonical\tkind\tsource\tstatus\tref\tnote\tconflict\tprimary\tuse_in_chat\n')
    for r in rows: fo.write('\t'.join(str(x).replace('\t', ' ').replace('\n', ' ') for x in r) + '\n')
with open(os.path.join(OUT, 'glossary/conflicts.tsv'), 'w', encoding='utf-8') as fo:
    fo.write('alias\tcandidates\n'); [fo.write(f'{a}\t{b}\n') for a, b in conflicts]
stats['aliases'] = len(rows); stats['alias_by_source'] = dict(collections.Counter(r[3] for r in rows))
stats['alias_conflicts'] = len(conflicts)
stats['alias_conflicts_curated'] = sum(1 for a, b in conflicts if 'repo_term' in b or 'manual_review' in b)

# ---------------- 3. 结构化事实 ----------------
SD = P('03-curated/bot-kb/02-结构化数据')
facts_cnt = collections.Counter()
def emit(fo, kind, fid, name, tier, version, source, attrs, answerable=True, flags=None):
    fo.write(json.dumps({'id': f'{kind}:{fid}', 'kind': kind, 'name': name, 'name_norm': norm(name), 'tier': tier,
        'version': version, 'source': source, 'answerable': answerable, 'flags': flags or [], 'attrs': attrs}, ensure_ascii=False) + '\n')
    facts_cnt[kind] += 1
with open(os.path.join(OUT, 'facts/client.jsonl'), 'w', encoding='utf-8') as fo:
    for kind, fn, namek in [('item', 'items', 'zh'), ('npc', 'npcs', 'name'), ('skill', 'skills', 'name'), ('boss', 'bosses', 'boss'), ('gem', 'gems', 'name'), ('gem_ref', 'gem_reference', 'name')]:
        for i, line in enumerate(open(os.path.join(SD, fn + '.jsonl'), encoding='utf-8')):
            d = json.loads(line); flags = []; ans = True
            name = d.get(namek) or d.get('name') or ''
            if kind == 'npc' and d.get('hp') == 99999: flags.append('hp_placeholder')
            if kind == 'gem' and not d.get('enabled', True): ans = False; flags.append('disabled')
            fid = d.get('id', f"{d.get('dungeon', '')}-{d.get('difficulty', '')}-{i}")
            tier = d.pop('tier', 'L1-Authoritative'); src = d.pop('source', fn)
            emit(fo, kind, fid, name, tier, '8.6.26.09', src, d, ans, flags)
with open(os.path.join(OUT, 'facts/nkwiki.jsonl'), 'w', encoding='utf-8') as fo:
    for kind, fn in [('wiki_equipment', 'equipment'), ('wiki_gem', 'gems'), ('wiki_gem_attr', 'gem_attributes'), ('wiki_fairy', 'fairies')]:
        for d in json.load(open(P('01-nkwiki/raw', fn + '.json'), encoding='utf-8')):
            if d.get('isDeleted'): continue
            attrs = {k: v for k, v in d.items() if k not in ('image', 'icon', 'isDeleted', 'deletedAt', 'createdAt', 'sortOrder', 'status', 'categoryId', 'seriesId')}
            emit(fo, kind, d['id'], d['name'], 'L2-nkwiki', d.get('updatedAt', '')[:10], 'nkwiki/' + fn, attrs)
stats['facts'] = dict(facts_cnt)

# ---------------- 4. 文档分块 ----------------
IMG = re.compile(r'!\[[^\]]*\]\([^)]*\)')
def clean(t):
    t = IMG.sub('', t.replace('\r\n', '\n'))
    t = re.sub(r'<[^>]+>', '', t)
    return re.sub(r'\n{3,}', '\n\n', t).strip()
def split_md(text):
    """按标题切节，返回 [(路径, 正文)]"""
    path, buf, out = [], [], []
    for line in text.split('\n'):
        m = re.match(r'^(#{1,6})\s*(.*)', line)
        if m:
            if ''.join(buf).strip(): out.append((' / '.join(x for _, x in path), '\n'.join(buf).strip()))
            buf = []; lv = len(m.group(1)); path = [p for p in path if p[0] < lv] + [(lv, m.group(2).strip())]
        else: buf.append(line)
    if ''.join(buf).strip(): out.append((' / '.join(x for _, x in path), '\n'.join(buf).strip()))
    return out
def pack(body, lo=300, hi=1000):
    """段落打包到 lo–hi 字；超长表格按行切并重复表头"""
    blocks, cur = [], []
    for para in re.split(r'\n\s*\n', body):
        lines = para.split('\n')
        if lines[0].lstrip().startswith('|') and len(para) > hi:
            head = lines[:2] if len(lines) > 1 and re.match(r'^\s*\|[\s:\-|]+\|\s*$', lines[1]) else lines[:1]
            rows, part = lines[len(head):], []
            for r in rows:
                if part and len('\n'.join(head + part + [r])) > hi: blocks.append('\n'.join(head + part)); part = []
                part.append(r)
            if part: blocks.append('\n'.join(head + part))
        elif len(para) > hi:
            s = ''
            for sent in re.split(r'(?<=[。！？；\n])', para):
                if len(s) + len(sent) > hi and s: blocks.append(s); s = ''
                s += sent
            if s: blocks.append(s)
        else: blocks.append(para)
    out, cur = [], ''
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > hi: out.append(cur); cur = b
        else: cur = (cur + '\n\n' + b) if cur else b
    if cur:
        if out and len(cur) < lo and len(out[-1]) + len(cur) <= hi * 1.2: out[-1] += '\n\n' + cur
        else: out.append(cur)
    return out
def shingles(t, n=8):
    t = norm(t); return {t[i:i + n] for i in range(max(1, len(t) - n + 1))}
FORBID = re.compile(r'(Q群|通讯作者|QQ[:：]?\s*\d{6,}|Q\d{6,}|token|密码|注入|反编译|协议包)', re.I)
accepted_sh = set(); chunks = []; dropped = collections.Counter(); docs_meta = []
def add_doc(doc_id, title, category, tier, source, version, last_verified, tags, text, publishable, dedupe=True, thr=0.85):
    raw = split_md(clean(text)) or [('', clean(text))]
    secs, acc = [], None   # 合并同一上级标题下的短小节（<300 字），避免碎块
    for sec, body in raw:
        parent = ' / '.join(sec.split(' / ')[:-1])
        if acc and acc[2] == parent and len(acc[1]) < 300:
            leaf = sec.split(' / ')[-1]
            acc = (acc[0] + '；' + leaf, acc[1] + f'\n\n**{leaf}**\n' + body, parent)
        else:
            if acc: secs.append(acc[:2])
            acc = (sec, body, parent)
    if acc: secs.append(acc[:2])
    kept = 0
    for sec, body in secs:
        if len(norm(body)) < 15: continue
        for i, part in enumerate(pack(body)):
            sh = shingles(part)
            if dedupe and sh and len(sh & accepted_sh) / len(sh) >= thr: dropped[source.split('/')[0] + ':dup'] += 1; continue
            accepted_sh.update(sh)
            ctype = 'table' if part.lstrip().startswith('|') else ('mixed' if '\n|' in part else 'text')
            cid = hashlib.sha1(f'{doc_id}|{sec}|{i}|{part[:50]}'.encode()).hexdigest()[:16]
            chunks.append({'chunk_id': cid, 'doc_id': doc_id, 'title': title, 'section': sec, 'category': category, 'tier': tier,
                'source': source, 'version': version, 'last_verified': last_verified, 'tags': tags, 'chunk_type': ctype,
                'char_count': len(part), 'publishable': publishable, 'sensitive': bool(FORBID.search(part)),
                'text': (f'【{title} / {sec}】\n' if sec else f'【{title}】\n') + part})
            kept += 1
    docs_meta.append([doc_id, title, source, version, tier, kept])
def frontmatter(t):
    m = re.match(r'^---\n(.*?)\n---\n', t, re.S)
    if not m: return {}, t
    return yaml.safe_load(m.group(1)) or {}, t[m.end():]
# 4.1 玩家库专题 12–18
for f in sorted(glob.glob(P('03-curated/bot-kb/01-玩家知识库/1[2-8]-*.md'))):
    fm, body = frontmatter(open(f, encoding='utf-8').read())
    n = os.path.basename(f)[:-3]
    add_doc('botkb:' + n, fm.get('title', n), fm.get('category', '玩家库'), fm.get('tier', 'L3'), fm.get('source', 'bot-kb/' + n),
            str(fm.get('version', '')), str(fm.get('last_verified', '')), fm.get('tags', []), body,
            publishable=not n.startswith(('12', '13')))
# 4.2 数据摘要 8.6.26.10（主版本）
v10 = open(P('06-production/evidence/v10-full.md'), encoding='utf-8').read()
add_doc('summary:8.6.26.10', '数据摘要', '数据摘要', 'L4', 'author/乱走丶沙城', '8.6.26.10', '2026-10-21', ['数据摘要'], v10, True)
# 4.3 nkwiki 攻略（与数据摘要重复的自动去掉）
for a in json.load(open(P('01-nkwiki/raw/articles.json'), encoding='utf-8')):
    if a.get('isDeleted') or not a.get('isPublished'): continue
    if re.match(r'^(第.+章|免责声明)', a.get('categoryName') or ''):   # 数据摘要转载，版本不明 → 以 v10 为准
        dropped['nkwiki:summary_repost'] += 1; docs_meta.append([f"nkwiki:article:{a['id']}", a['title'], 'nkwiki/articles', 'repost', 'L2', 0]); continue
    add_doc(f"nkwiki:article:{a['id']}", a['title'], a.get('categoryName') or 'nkwiki', 'L2', 'nkwiki/articles', (a.get('updatedAt') or '')[:10],
            '2026-10-22', [a.get('categoryName') or ''], a.get('content') or '', False)
for k, v in enc.items():
    add_doc(f'nkwiki:encyclopedia:{k}', v['title'], '百科', 'L2', 'nkwiki/encyclopedia', (v.get('updatedAt') or '')[:10], '2026-10-22', ['百科'], v.get('content') or '', False)
# 4.4 社区仓库 knowledge（多为 v10 切片；只留不重复的）
for f in sorted(glob.glob(P('03-curated/naikuai-base-knowledge/knowledge/*/*.json'))):
    d = json.load(open(f, encoding='utf-8'))
    add_doc(d['id'], d['title'], d.get('category', ''), 'L4', 'repo/knowledge', '8.6.26.10', '', d.get('tags', []), d.get('content', ''), True)
# 4.5 对照：玩家库 01–11（8.6.26.09）有多少内容 v10 里没有 —— 只统计，不入库
old_unique = 0; old_total = 0
for f in sorted(glob.glob(P('03-curated/bot-kb/01-玩家知识库/0[1-9]-*.md')) + glob.glob(P('03-curated/bot-kb/01-玩家知识库/1[01]-*.md'))):
    _, body = frontmatter(open(f, encoding='utf-8').read())
    for sec, b in split_md(clean(body)):
        for part in pack(b):
            sh = shingles(part); old_total += 1
            if sh and len(sh & accepted_sh) / len(sh) < 0.6: old_unique += 1
stats['botkb_09_chunks'] = old_total; stats['botkb_09_chunks_not_in_v10'] = old_unique
with open(os.path.join(OUT, 'docs/chunks.jsonl'), 'w', encoding='utf-8') as fo:
    for ch in chunks: fo.write(json.dumps(ch, ensure_ascii=False) + '\n')
with open(os.path.join(OUT, 'docs/documents.tsv'), 'w', encoding='utf-8') as fo:
    fo.write('doc_id\ttitle\tsource\tversion\ttier\tkept_chunks\n'); [fo.write('\t'.join(map(str, d)) + '\n') for d in docs_meta]
stats['chunks'] = len(chunks); stats['chunks_by_source'] = dict(collections.Counter(c['source'] for c in chunks))
stats['chunks_dropped_dup'] = dict(dropped); stats['chunks_sensitive'] = sum(c['sensitive'] for c in chunks)
lens = sorted(c['char_count'] for c in chunks); stats['chunk_len_p10_p50_p90'] = [lens[len(lens) // 10], lens[len(lens) // 2], lens[len(lens) * 9 // 10]]
stats['nkwiki_articles_kept'] = sum(1 for d in docs_meta if d[0].startswith('nkwiki:article') and d[5] > 0)
stats['repo_knowledge_kept_docs'] = sum(1 for d in docs_meta if d[0].startswith('knowledge.') and d[5] > 0)

# ---------------- 5. SQLite ----------------
db = os.path.join(OUT, 'build/naikuai-kb.sqlite')
if os.path.exists(db): os.remove(db)
c = sqlite3.connect(db)
c.executescript('''
CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, doc_id TEXT, title TEXT, section TEXT, category TEXT, tier TEXT, source TEXT,
  version TEXT, chunk_type TEXT, char_count INT, publishable INT, sensitive INT, text TEXT);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, title, section, content='chunks', content_rowid='rowid', tokenize='trigram');
CREATE TABLE aliases(alias TEXT, alias_norm TEXT, canonical TEXT, canonical_norm TEXT, kind TEXT, source TEXT, status TEXT, ref TEXT, note TEXT, conflict INT, primary_ INT, use_in_chat INT);
CREATE INDEX ix_alias ON aliases(alias_norm);
CREATE TABLE facts(id TEXT PRIMARY KEY, kind TEXT, name TEXT, name_norm TEXT, tier TEXT, version TEXT, source TEXT, answerable INT, flags TEXT, attrs TEXT);
CREATE INDEX ix_fact_name ON facts(name_norm);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
''')
c.executemany('insert into chunks values(?,?,?,?,?,?,?,?,?,?,?,?,?)', [(x['chunk_id'], x['doc_id'], x['title'], x['section'], x['category'], x['tier'], x['source'], x['version'], x['chunk_type'], x['char_count'], int(x['publishable']), int(x['sensitive']), x['text']) for x in chunks])
c.execute("insert into chunks_fts(rowid,text,title,section) select rowid,text,title,section from chunks")
c.executemany('insert into aliases values(?,?,?,?,?,?,?,?,?,?,?,?)', [(r[0], norm(r[0]), r[1], norm(r[1]), r[2], r[3], r[4], r[5], r[6], int(r[7] == 'conflict'), int(r[8] == 'primary'), int(r[9] == 'yes')) for r in rows])
for fn in ['client', 'nkwiki']:
    for line in open(os.path.join(OUT, f'facts/{fn}.jsonl'), encoding='utf-8'):
        d = json.loads(line)
        c.execute('insert or ignore into facts values(?,?,?,?,?,?,?,?,?,?)', (d['id'], d['kind'], d['name'], d['name_norm'], d['tier'], d['version'], d['source'], int(d['answerable']), json.dumps(d['flags'], ensure_ascii=False), json.dumps(d['attrs'], ensure_ascii=False)))
c.executemany('insert into meta values(?,?)', [('built_from', os.path.basename(SRC.rstrip('/'))), ('summary_version', '8.6.26.10'), ('client_version', '8.6.26.09'), ('stats', json.dumps(stats, ensure_ascii=False))])
c.commit(); c.execute('VACUUM'); c.close()
json.dump(stats, open(os.path.join(OUT, 'stats.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(stats, ensure_ascii=False, indent=1))

# ---------------- 6. 第二轮修正（状态、组合实体、多义词、公开标记、统计口径）----------------
import subprocess
subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'refine.py'), OUT], check=True)
