#!/usr/bin/env python3
"""公开知识库只读检索引擎 (kb_query_public.py)
特点：
- 只读与不可变安全模式：采用 SQLite URI (?mode=ro&immutable=1)，杜绝并发写冲突与越权修改
- 全量参数化检索：彻底杜绝 SQL 注入风险
- 实体消歧反问接口：针对多义词（如“碎片”）提供确定性反问提示 (action="clarify") 与上下文消歧
- Trigram 全文检索与覆盖度重排：支持游戏缩写与黑话子串容错搜索
- 严格相关度评分与门禁：当 top 结果评分过低或无关键词强匹配时，精准判定为未收录，杜绝无关误召回
"""
import os, sys, re, json, sqlite3, unicodedata, time
from pathlib import Path

DEFAULT_DB = str(Path(__file__).resolve().parent.parent / "naikuai-kb-public.sqlite")

def norm(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub(r'[\s·・\-_/|\\,，。、:：;；"“”\'‘’()（）\[\]【】<>《》!！?？*#`~]+', '', s)

STOP = sorted(['卖什么', '怎么样', '怎么打', '怎么', '如何', '什么', '多少', '哪里', '哪个', '哪些', '几个', '为什么', '是不是', '有没有', '能不能',
               '可以', '请问', '一下', '大佬', '求问', '知道', '告诉我', '是啥', '咋打', '咋', '吗', '呢', '啊', '吧', '呀', '的', '了', '是', '有',
               '技能', '打法', '攻略', '机制', '教学', '教程', '属性'], key=len, reverse=True)

PROFESSIONS = ('猎魔人', '魔导师', '召唤师', '占星师', '弓箭手', '影刃', '龙骑士', '骑士')


def question_plan(question):
    """Route explicit topics to evidence sections; never invent missing facts."""
    q = norm(question)
    if ('宝石' in q and any(t in q for t in ('怎么打', '咋打', '如何打'))
            and not any(t in q for t in ('镶嵌', '升级', '效果', '切割'))):
        return {'kind': 'gem_ambiguous', 'prompt': '你指宝石镶嵌、升级，还是某种宝石的效果？'}
    if '宝石' in q and '镶嵌' in q:
        return {'kind': 'inlay', 'query': '宝石镶嵌', 'section': '%镶嵌%'}
    if '宝石' in q and '升级' in q:
        return {'kind': 'gem_upgrade', 'query': '宝石升级材料', 'section': '%宝石升级材料%'}
    if '宝石' in q and any(t in q for t in ('效果', '属性')):
        term = re.sub(r'请问|是多少|是什么|宝石|效果|属性|什么|的', '', q)
        return {'kind': 'gem_effect', 'query': term or '宝石效果', 'section': '%2.6宝石%'}
    if '无尽' in q and re.search(r'(三|3)(人|个)(小)?(副本|本|秘境)', q):
        return {'kind': 'sea_three', 'query': '无尽之海', 'section': '%第四章%无尽之海%'}
    if '海拉' in q and not any(t in q for t in ('庇护', '琳')):
        five = bool(re.search(r'(?<![一二三四五六七八九十])五人|(?<!\d)5人', q))
        ten = bool(re.search(r'(?<![一二三四五六七八九十])十人|(?<!\d)10人|精英', q))
        if five == ten:
            return {'kind': 'hela_ambiguous', 'prompt': '你指五人海拉，还是十人精英海拉？'}
        return {'kind': 'hela_ten' if ten else 'hela_five', 'query': '海拉', 'section': '%悲惨宫殿%'}
    if (not any(t in q for t in ('npc', '怪物'))
            and any(t in q for t in ('怎么玩', '如何玩', '玩法', '技能', '连招', '加点', '怎么输出', '教学'))):
        for profession in PROFESSIONS:
            if profession in q:
                return {'kind': 'profession', 'query': profession, 'section': '%对各职业的深刻理解%'}
    return None


def route_accepts(plan, section, body):
    kind = plan['kind']
    leaf = section.split('/')[-1]
    if kind == 'sea_three':
        return bool(re.search(r'\|\s*3\s*-\s*3\s*\|', body))
    if kind.startswith('hela_'):
        return ('精英' in leaf) == (kind == 'hela_ten')
    if kind == 'profession':
        return leaf.endswith(plan['query'])
    if kind == 'gem_effect':
        return '效果' in section or '解释' in section
    return True

class PublicKB:
    def __init__(self, db_path=None):
        self.path = db_path or DEFAULT_DB
        uri = Path(self.path).resolve().as_uri() + '?mode=ro&immutable=1'
        self.conn = sqlite3.connect(uri, uri=True, timeout=0.25)
        self.dict = {}
        for an, canon, res in self.conn.execute('SELECT alias_norm, canonical, resolution FROM aliases WHERE use_in_chat=1 AND publishable=1 ORDER BY primary_ DESC, canonical'):
            e = self.dict.setdefault(an, {'canon': [], 'res': res, 'alias': True})
            if canon not in e['canon']:
                e['canon'].append(canon)
        for (n,) in self.conn.execute('SELECT DISTINCT name FROM facts WHERE answerable=1 AND publishable=1 AND length(name_norm) BETWEEN 2 AND 16 ORDER BY name'):
            self.dict.setdefault(norm(n), {'canon': [n], 'res': 'name', 'alias': False})
        self.groups = {a: json.loads(m) for a, m in self.conn.execute('SELECT alias_norm, members FROM alias_groups')}
        self.senses = {}
        for a, s, kw in self.conn.execute('SELECT alias_norm, sense, keywords FROM alias_senses ORDER BY rowid'):
            self.senses.setdefault(a, []).append((s, kw.split('|') if kw else []))
        self.maxlen = max(map(len, self.dict)) if self.dict else 2
        self.meta = dict(self.conn.execute('SELECT key, value FROM meta'))

    def resolve(self, q):
        qn, i, hits, clarify = norm(q), 0, [], []
        while i < len(qn):
            for L in range(min(self.maxlen, len(qn) - i), 1, -1):
                e = self.dict.get(qn[i:i + L])
                if e:
                    break
            else:
                i += 1
                continue
            sur = qn[i:i + L]
            if sur in self.senses:
                pick = [s for s, kws in self.senses[sur] if any(k in qn for k in kws)]
                if len(pick) == 1:
                    hits.append({'surface': sur, 'names': pick, 'type': 'ambiguous_resolved'})
                else:
                    clarify.append({'surface': sur, 'options': [s for s, _ in self.senses[sur]]})
            elif sur in self.groups:
                hits.append({'surface': sur, 'names': self.groups[sur], 'type': 'group'})
            else:
                hits.append({'surface': sur, 'names': [p.strip() for c in e['canon'] for p in re.split(r'\s*[/／，,+]\s*', c) if p.strip()],
                             'type': 'alias' if e['alias'] else 'name'})
            i += L
        return hits, clarify

    def facts(self, names, max_n=6, budget=1200, per_fact=600):
        out, skipped, used, seen = [], [], 0, set()
        for n in names:
            rs = self.conn.execute('SELECT id,kind,name,tier,version,source,flags,attrs FROM facts WHERE name_norm=? AND answerable=1 AND publishable=1', (norm(n),)).fetchall()
            for r in rs:
                if r[0] in seen or len(out) >= max_n:
                    continue
                seen.add(r[0])
                d = dict(zip(['id', 'kind', 'name', 'tier', 'version', 'source', 'flags', 'attrs'], r))
                d['flags'] = json.loads(d['flags']) if d['flags'] else []
                text = str(d.pop('attrs', ''))
                if len(text) > per_fact:
                    text = text[:per_fact] + '…（已截断）'
                    d['truncated'] = True
                if used + len(text) > budget:
                    skipped.append(d['id'])
                    continue
                d['text'] = text
                out.append(d)
                used += len(text)
        return out

    def _segments(self, q):
        t = norm(q)
        for s in STOP:
            t = t.replace(s, ' ')
        raw_segs = [s for s in t.split() if len(s) >= 2]
        if not raw_segs and len(norm(q)) >= 2:
            raw_segs = [norm(q)]
        return raw_segs

    def chunks(self, q, names, k=4, per_chunk=900, budget=2400):
        plan = question_plan(q)
        if plan and 'query' in plan:
            q = plan['query']
            names = [q]
        segs = self._segments(q)
        ent = [norm(n) for n in names if len(norm(n)) >= 2]
        tri = sorted({s[i:i + 3] for s in segs + ent for i in range(len(s) - 2)})
        bi = sorted({s[i:i + 2] for s in segs + ent for i in range(len(s) - 1)})
        cand = {}

        def fts(expr, lim):
            if not expr: return
            try:
                for rid, rank in self.conn.execute(
                    'SELECT f.rowid, bm25(chunks_fts) FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid '
                    'WHERE chunks_fts MATCH ? AND c.sensitive=0 AND c.review_status="active" AND c.publishable=1 '
                    'ORDER BY bm25(chunks_fts) LIMIT ?', (expr, lim)
                ):
                    cand.setdefault(rid, rank)
            except sqlite3.OperationalError:
                pass

        q3 = lambda xs: ' OR '.join('"%s"' % x.replace('"', '') for x in xs if len(x) >= 3)
        fts(q3(ent), 40)
        fts(q3(tri), 60)

        for x in bi:
            for (rid,) in self.conn.execute(
                'SELECT c.rowid FROM chunks c WHERE (c.section LIKE ? OR c.text LIKE ?) AND c.sensitive=0 AND c.review_status="active" AND c.publishable=1 '
                'ORDER BY c.rowid LIMIT 30', ('%' + x + '%', '%' + x + '%')
            ):
                cand.setdefault(rid, 0.0)

        if plan and 'section' in plan:
            # Filters apply at candidate collection, before ranking and limits.
            candidates = self.conn.execute(
                'SELECT rowid,section,text FROM chunks WHERE section LIKE ? '
                'AND sensitive=0 AND review_status="active" AND publishable=1 ORDER BY chunk_id',
                (plan['section'],)).fetchall()
            cand = {rid: 0.0 for rid, section, body in candidates if route_accepts(plan, section, body)}

        if not cand:
            return []

        placeholders = ','.join('?' for _ in cand)
        rows = self.conn.execute(
            f'SELECT rowid,chunk_id,doc_id,title,section,source,version,tier,review_status,status_note,text '
            f'FROM chunks WHERE rowid IN ({placeholders})', list(cand.keys())
        ).fetchall()

        valid_rows = []
        for r in rows:
            head, body = norm(r[3] + r[4]), norm(r[10])
            s_ent_head = sum(1 for e in ent if e in head)
            s_ent_body = sum(1 for e in ent if e in body)
            s_seg_head = sum(1 for s in segs if s in head)
            s_seg_body = sum(1 for s in segs if s in body)

            cov = lambda gs, t: sum(g in t for g in gs) / len(gs) if gs else 0.0
            cov_tri_body = cov(tri, body)
            cov_tri_head = cov(tri, head)
            cov_bi_body = cov(bi, body)
            cov_bi_head = cov(bi, head)
            tf_bi_body = min(15, sum(body.count(b) for b in bi)) if bi else 0

            # 相关度门禁判断
            if ent:
                if s_ent_head == 0 and s_ent_body == 0:
                    continue
            else:
                has_seg_match = (s_seg_head > 0 or s_seg_body > 0)
                has_tri_match = (cov_tri_head > 0 or cov_tri_body > 0)
                if tri and not has_tri_match and not has_seg_match:
                    continue
                strong_head = (s_seg_head > 0 or cov_bi_head >= 0.5)
                strong_body = (has_seg_match and cov_bi_body >= 0.3) or (cov_tri_body >= 0.25 and cov_bi_body >= 0.35)
                if not (strong_head or strong_body):
                    continue

            # 末级标题精准度加分 (优先返回匹配的小节本体，避免父级或精英怪干扰)
            leaf_bonus = 0.0
            leaf_section = norm(r[4].split('/')[-1])
            is_elite = ('精英' in leaf_section or '精英' in head)
            query_has_elite = any('精英' in s for s in segs)
            clean_leaf = re.sub(r'^\d+(\.\d+)*', '', leaf_section)
            if any(s in leaf_section for s in segs):
                if is_elite and not query_has_elite:
                    leaf_bonus += 2.0
                else:
                    leaf_bonus += 8.0
                    if clean_leaf and any(s == clean_leaf for s in segs):
                        leaf_bonus += 15.0

            rel_score = (
                20.0 * s_ent_head + 10.0 * s_ent_body +
                12.0 * s_seg_head + 6.0 * s_seg_body +
                6.0 * cov_tri_head + 4.0 * cov_tri_body +
                4.0 * cov_bi_head + 2.0 * cov_bi_body +
                0.5 * tf_bi_body + leaf_bonus
            )
            if rel_score < 3.0:
                continue
            valid_rows.append((r, rel_score, cand.get(r[0], 0.0)))

        if not valid_rows:
            return []

        valid_rows.sort(key=lambda item: (-item[1], item[2], item[0][1]))

        out, used, docs = [], 0, {}
        for r, score_val, _ in valid_rows:
            if len(out) >= k or used >= budget:
                break
            doc_key = r[4] if plan and plan['kind'] == 'sea_three' else r[2]
            if docs.get(doc_key, 0) >= (1 if plan and plan['kind'] == 'sea_three' else 2):
                continue
            available = min(per_chunk, budget - used)
            text = (r[10][:available] if plan and plan['kind'] == 'sea_three'
                    else self._passage(r[10], ent + tri + bi, available))
            if not text:
                continue
            docs[doc_key] = docs.get(doc_key, 0) + 1
            used += len(text)
            out.append({
                'chunk_id': r[1], 'doc_id': r[2], 'title': r[3], 'section': r[4],
                'source': r[5], 'version': r[6], 'tier': r[7], 'review_status': r[8],
                'status_note': r[9], 'score': round(score_val, 2),
                'truncated': len(text) < len(r[10]), 'text': text
            })
        return out

    @staticmethod
    def _passage(text, terms, limit):
        if len(text) <= limit:
            return text
        lines = text.split('\n')
        head = [lines[0]]
        rest = lines[1:]
        if len(rest) > 1 and rest[0].lstrip().startswith('|') and re.match(r'^\s*\|[\s:\-|]+\|\s*$', rest[1]):
            head += rest[:2]
            rest = rest[2:]
        cut = []
        for l in rest:
            if len(l) > limit * 0.8:
                ln = norm(l)
                pos = min([ln.find(t) for t in terms if t in ln] or [0])
                st = max(0, int(pos * len(l) / max(1, len(ln))) - limit // 4)
                l = ('…' if st else '') + l[st:st + int(limit * 0.7)] + '…'
            cut.append(l)
        rest = cut
        sc = [sum(t in norm(l) for t in terms) for l in rest]
        if not any(sc):
            return ''
        order = sorted(range(len(rest)), key=lambda i: (-sc[i], i))
        keep, size = set(), len('\n'.join(head))
        for i in order:
            for j in (i, i - 1, i + 1):
                if 0 <= j < len(rest) and j not in keep and size + len(rest[j]) + 1 <= limit:
                    keep.add(j)
                    size += len(rest[j]) + 1
            if size >= limit * 0.9:
                break
        out, prev = list(head), None
        for i in sorted(keep):
            if prev is not None and i != prev + 1:
                out.append('…')
            out.append(rest[i])
            prev = i
        return '\n'.join(out)

    def lookup(self, q, **kw):
        t = time.perf_counter()
        plan = question_plan(q)
        hits, clarify = self.resolve(q)
        names = list(dict.fromkeys(n for h in hits for n in h['names']))
        res = {
            'query': q,
            'action': 'answer',
            'terms': hits,
            'clarify': clarify,
            'facts': [],
            'chunks': [],
            'summary_version': self.meta.get('summary_version'),
            'summary_review_due': self.meta.get('summary_review_due'),
            'content_scope': self.meta.get('content_scope')
        }
        if plan:
            res['retrieval_intent'] = plan['kind']
        if plan and 'prompt' in plan:
            res.update(action='clarify', prompt=plan['prompt'], terms=[], clarify=[{
                'surface': q, 'options': (['宝石镶嵌', '宝石升级', '宝石效果'] if plan['kind'] == 'gem_ambiguous'
                                          else ['五人海拉', '十人精英海拉'])}])
            res['ms'] = round((time.perf_counter() - t) * 1000, 1)
            return res
        if clarify:
            res['action'] = 'clarify'
            res['prompt'] = f"请问您指的是【{'】、【'.join(clarify[0]['options'])}】？"
            res['ms'] = round((time.perf_counter() - t) * 1000, 1)
            return res

        res['facts'] = self.facts(names)
        res['chunks'] = self.chunks(q, names, **kw)

        # 结果判空与未收录提示：若既无 facts 也无 chunks，且未匹配已知实体 terms，标记为 not_found
        if not res['facts'] and not res['chunks'] and not res['terms']:
            res['action'] = 'not_found'
            res['message'] = '未收录相关知识'

        res['ms'] = round((time.perf_counter() - t) * 1000, 1)
        return res

if __name__ == '__main__':
    query_str = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else '魔导师技能'
    kb = PublicKB()
    result = kb.lookup(query_str)
    print(json.dumps(result, ensure_ascii=False, indent=2))
