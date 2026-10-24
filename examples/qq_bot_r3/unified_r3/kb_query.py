#!/usr/bin/env python3
"""检索层（替代第一轮的 query.py 示例）。规则在代码里执行，不靠模型理解标记：
- 事实只返回 answerable=1；文档只返回 review_status='active' 且 sensitive=0（legacy 需显式 include_legacy，返回时带说明）
- public_only=True 时 facts / aliases / chunks 三处都按 publishable 过滤（publishable 只是内部标记，不是授权依据）
- 别名：逐位置最长匹配；group 返回全部成员；ambiguous 有消歧词才落到某个义项，否则返回 clarify 让上层反问
- 召回：实体词 FTS 短语 + 问句去停用词后的三字片段 OR 检索 + 二字词 LIKE，合并后按覆盖度打分
- 输出：块 ID + 完整块或命中段落（按单块/总字数预算截取），排序固定，可复现
用法: python3 kb_query.py "大海拉P2怎么打" [--public] [--legacy]"""
import os, re, sys, json, sqlite3, unicodedata, time
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'build/naikuai-kb.sqlite')
def norm(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[\s·・\-_/|\\,，。、:：;；"“”\'‘’()（）\[\]【】<>《》!！?？*#`~]+', '', s)
STOP = sorted(['怎么样', '怎么打', '怎么', '如何', '什么', '多少', '哪里', '哪个', '哪些', '几个', '为什么', '是不是', '有没有', '能不能',
               '可以', '请问', '一下', '大佬', '求问', '知道', '告诉我', '是啥', '咋打', '咋', '吗', '呢', '啊', '吧', '呀', '的', '了', '是', '有'], key=len, reverse=True)
DROP_KEYS = {'image', 'icon', 'itemImage', 'cover', 'tierColor', 'categoryColor', 'categorySlug', 'updatedAt', 'createdAt', 'raw_id',
             'extra_count_raw', 'fix_item_raw', 'buff_ids', 'gravity', 'ai', 'talk', 'capture', 'mount_after', 'name_internal'}
def compact(attrs):
    """事实属性 → 精简文本：去图片/内部字段/空值，列表里的对象压成「名称:值/说明」"""
    def val(v):
        if isinstance(v, dict):
            if 'materials' in v or 'template' in v:
                ms = '、'.join(f"{m.get('itemName', '')}×{m.get('count', '')}" for m in v.get('materials') or [])
                return ms or v.get('template') or ''
            return '；'.join(f'{k}={val(x)}' for k, x in v.items() if k not in DROP_KEYS and val(x) not in ('', None))
        if isinstance(v, list):
            parts = []
            for x in v:
                if isinstance(x, dict):
                    nm = x.get('name') or x.get('type') or ''
                    rest = x.get('description') or x.get('value')
                    if 'unlocked' in x and not rest: rest = '已解锁' if x['unlocked'] else '未解锁'
                    parts.append(f'{nm}:{rest}' if rest not in ('', None) else str(nm))
                else: parts.append(str(x))
            return '，'.join(p for p in parts if p)
        return '' if v in (None, '{}', '[]') else str(v)
    out = []
    for k, v in attrs.items():
        if k in DROP_KEYS: continue
        t = val(v)
        if t and not re.fullmatch(r'[a-z0-9_]+:[a-z0-9_.]+', t): out.append(f'{k}={t}')   # 内部英文 ID 不送模型
    return '；'.join(out)
KIND_ORDER = {'boss': 0, 'gem': 1, 'wiki_gem': 2, 'wiki_gem_attr': 3, 'wiki_equipment': 4, 'wiki_fairy': 5, 'item': 6, 'skill': 7, 'gem_ref': 8, 'npc': 9}

class KB:
    def __init__(self, path=DB, public_only=False):
        self.c = sqlite3.connect(__import__('pathlib').Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True, timeout=0.25); self.public = public_only
        pub = ' and publishable=1' if public_only else ''
        self.dict = {}   # norm -> {'canon': [...], 'res': resolution, 'alias': bool}
        for an, canon, res in self.c.execute(f'select alias_norm, canonical, resolution from aliases where use_in_chat=1{pub} order by primary_ desc, source, canonical'):
            e = self.dict.setdefault(an, {'canon': [], 'res': res, 'alias': True})
            if canon not in e['canon']: e['canon'].append(canon)
        fpub = ' and publishable=1' if public_only else ''
        for (n,) in self.c.execute(f'select distinct name from facts where answerable=1{fpub} and length(name_norm) between 2 and 16 order by name'):
            self.dict.setdefault(norm(n), {'canon': [n], 'res': 'name', 'alias': False})
        self.groups = {a: json.loads(m) for a, m in self.c.execute('select alias_norm, members from alias_groups')}
        self.senses = {}
        for a, s, kw in self.c.execute('select alias_norm, sense, keywords from alias_senses order by rowid'):
            self.senses.setdefault(a, []).append((s, kw.split('|')))
        self.maxlen = max(map(len, self.dict)) if self.dict else 2
        self.meta = dict(self.c.execute('select key, value from meta'))

    # ---- 1. 术语 ----
    def resolve(self, q):
        qn, i, hits, clarify = norm(q), 0, [], []
        while i < len(qn):
            for L in range(min(self.maxlen, len(qn) - i), 1, -1):
                e = self.dict.get(qn[i:i + L])
                if e: break
            else:
                i += 1; continue
            sur = qn[i:i + L]
            if sur in self.senses:
                pick = [s for s, kws in self.senses[sur] if any(k in qn for k in kws)]
                if len(pick) == 1: hits.append({'surface': sur, 'names': pick, 'type': 'ambiguous_resolved'})
                else: clarify.append({'surface': sur, 'options': [s for s, _ in self.senses[sur]]})
            elif sur in self.groups: hits.append({'surface': sur, 'names': self.groups[sur], 'type': 'group'})
            else: hits.append({'surface': sur, 'names': [p.strip() for c in e['canon'] for p in re.split(r'\s*[/／，,+]\s*', c) if p.strip()],
                               'type': 'alias' if e['alias'] else 'name'})
            i += L
        return hits, clarify

    # ---- 2. 事实 ----
    def facts(self, names, max_n=6, budget=1200, per_fact=600):
        """先精简再计预算；单条装不下就跳过（记入 skipped），不影响后面的候选"""
        out, skipped, used, seen = [], [], 0, set()
        pub = ' and publishable=1' if self.public else ''
        per_name = []
        for n in names:
            rs = self.c.execute(f'select id,kind,name,tier,version,source,flags,attrs from facts where name_norm=? and answerable=1{pub}', (norm(n),)).fetchall()
            per_name.append(sorted(rs, key=lambda r: (KIND_ORDER.get(r[1], 99), r[0]))[:3])
        # 轮转：先给每个名称各取 1 条，再取第 2 条……避免前面的名称占满条数
        order = [lst[i] for i in range(3) for lst in per_name if i < len(lst)]
        for r in order:
            if r[0] in seen or len(out) >= max_n: continue
            seen.add(r[0])
            d = dict(zip(['id', 'kind', 'name', 'tier', 'version', 'source', 'flags', 'attrs'], r))
            d['flags'] = json.loads(d['flags'])
            text = compact(json.loads(d.pop('attrs')))
            if len(text) > per_fact:
                text = text[:per_fact] + '…（已截断，完整字段见 id）'; d['truncated'] = True
            if used + len(text) > budget:
                skipped.append(d['id']); continue
            d['text'] = text; out.append(d); used += len(text)
        self._skipped = skipped
        return out

    # ---- 3. 文档 ----
    def _segments(self, q):
        t = norm(q)
        for s in STOP: t = t.replace(s, ' ')
        return [s for s in t.split() if len(s) >= 2]
    def chunks(self, q, names, k=4, per_chunk=900, budget=2400, include_legacy=False):
        segs = self._segments(q)
        ent = [norm(n) for n in names if len(norm(n)) >= 2]
        tri = sorted({s[i:i + 3] for s in segs + ent for i in range(len(s) - 2)})
        bi = sorted({s[i:i + 2] for s in segs + ent for i in range(len(s) - 1)})
        grams = tri + bi
        st = "('active','legacy')" if include_legacy else "('active')"
        where = f"c.sensitive=0 and c.review_status in {st}" + (' and c.publishable=1' if self.public else '')
        cand = {}
        def fts(expr, lim):
            if not expr: return
            try:
                for rid, rank in self.c.execute(f'select f.rowid, bm25(chunks_fts) from chunks_fts f join chunks c on c.rowid=f.rowid where chunks_fts match ? and {where} order by bm25(chunks_fts) limit ?', (expr, lim)):
                    cand.setdefault(rid, rank)
            except sqlite3.OperationalError: pass
        q3 = lambda xs: ' OR '.join('"%s"' % x.replace('"', '') for x in xs if len(x) >= 3)
        fts(q3(ent), 40); fts(q3(tri), 60)
        for x in bi:
            for (rid,) in self.c.execute(f'select c.rowid from chunks c where c.text like ? and {where} order by c.rowid limit 30', ('%' + x + '%',)):
                cand.setdefault(rid, 0.0)
        if not cand: return []
        rows = self.c.execute(f"select rowid,chunk_id,doc_id,title,section,source,version,tier,review_status,status_note,text from chunks where rowid in ({','.join(map(str, cand))})").fetchall()
        def score(r):
            head, body = norm(r[3] + r[4]), norm(r[10])
            s_ent = sum(3 * (e in head) + 1 * (e in body) for e in ent)
            cov = lambda gs, t: sum(g in t for g in gs) / len(gs) if gs else 0
            s_cov = cov(tri, body) * 2 + cov(bi, body) + 2 * cov(bi, head)
            return (-(s_ent + 3 * s_cov), cand[r[0]], r[1])   # 分高者先；同分按 bm25、块 ID，保证顺序固定
        rows.sort(key=score)
        out, used, docs = [], 0, {}
        for r in rows:
            if len(out) >= k or used >= budget: break
            if docs.get(r[2], 0) >= 2: continue          # 同一文档最多 2 块，避免一篇刷屏
            text = self._passage(r[10], ent + tri + bi, min(per_chunk, budget - used))
            if not text: continue
            docs[r[2]] = docs.get(r[2], 0) + 1; used += len(text)
            out.append({'chunk_id': r[1], 'doc_id': r[2], 'title': r[3], 'section': r[4], 'source': r[5], 'version': r[6], 'tier': r[7],
                        'review_status': r[8], 'status_note': r[9], 'truncated': len(text) < len(r[10]), 'text': text})
        return out
    @staticmethod
    def _passage(text, terms, limit):
        """块不超预算就整块给；超了就保留标题行（和表头），再按命中词取相邻段落/表格行"""
        if len(text) <= limit: return text
        lines = text.split('\n'); head = [lines[0]]; rest = lines[1:]
        if len(rest) > 1 and rest[0].lstrip().startswith('|') and re.match(r'^\s*\|[\s:\-|]+\|\s*$', rest[1]): head += rest[:2]; rest = rest[2:]
        cut = []                                         # 单行超预算（长表格行/长段落）→ 按命中位置开窗
        for l in rest:
            if len(l) > limit * 0.8:
                ln = norm(l); pos = min([ln.find(t) for t in terms if t in ln] or [0])
                st = max(0, int(pos * len(l) / max(1, len(ln))) - limit // 4)
                l = ('…' if st else '') + l[st:st + int(limit * 0.7)] + '…'
            cut.append(l)
        rest = cut
        sc = [sum(t in norm(l) for t in terms) for l in rest]
        if not any(sc): return ''                       # 截取后没有任何命中词，不送给模型
        order = sorted(range(len(rest)), key=lambda i: (-sc[i], i))
        keep, size = set(), len('\n'.join(head))
        for i in order:
            for j in (i, i - 1, i + 1):
                if 0 <= j < len(rest) and j not in keep and size + len(rest[j]) + 1 <= limit:
                    keep.add(j); size += len(rest[j]) + 1
            if size >= limit * 0.9: break
        out, prev = list(head), None
        for i in sorted(keep):
            if prev is not None and i != prev + 1: out.append('…')
            out.append(rest[i]); prev = i
        return '\n'.join(out)

    def lookup(self, q, **kw):
        t = time.perf_counter()
        hits, clarify = self.resolve(q)
        names = list(dict.fromkeys(n for h in hits for n in h['names']))
        res = {'query': q, 'terms': hits, 'clarify': clarify, 'facts': [], 'facts_skipped': [], 'chunks': [],
               'summary_version': self.meta.get('summary_version'), 'summary_review_due': self.meta.get('summary_review_due')}
        if clarify:   # 有未消歧的多义词：不检索，交给上层反问（不把猜测的资料送进模型）
            res['action'] = 'clarify'; res['versions'] = []; res['ms'] = round((time.perf_counter() - t) * 1000, 1)
            return res
        res['action'] = 'answer'
        res['facts'] = self.facts(names); res['facts_skipped'] = self._skipped
        res['chunks'] = self.chunks(q, names, **kw)
        res['versions'] = sorted({c['version'] for c in res['chunks']} | {f['version'] for f in res['facts']})
        res['ms'] = round((time.perf_counter() - t) * 1000, 1)
        return res

if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    kb = KB(public_only='--public' in sys.argv)
    print(json.dumps(kb.lookup(' '.join(a) or '魂碎是什么', include_legacy='--legacy' in sys.argv), ensure_ascii=False, indent=1))
