import os, sys, unittest, sqlite3
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from kb_query import KB
DB = os.path.join(os.environ["UNIFIED_CANDIDATE_DIR"], "build/naikuai-kb.sqlite")
c = sqlite3.connect(DB)
class Invariants(unittest.TestCase):
    kb = KB(DB)
    def test_unanswerable_facts_never_returned(self):
        names = [n for (n,) in c.execute('select name from facts where answerable=0')]
        self.assertTrue(names)
        ids = {i for (i,) in c.execute('select id from facts where answerable=0')}
        for n in names:
            self.assertFalse({f['id'] for f in self.kb.facts([n])} & ids, n)
            self.assertFalse({f['id'] for f in self.kb.lookup(n + '宝石效果')['facts']} & ids, n)
    def test_candidate_and_sensitive_chunks_never_returned(self):
        bad = {r[0] for r in c.execute("select chunk_id from chunks where review_status!='active' or sensitive=1")}
        for (t, s) in c.execute("select title, section from chunks where review_status!='active' or sensitive=1"):
            got = {x['chunk_id'] for x in self.kb.lookup(t + ' ' + s)['chunks']}
            self.assertFalse(got & bad, t)
    def test_legacy_only_on_request_and_labeled(self):
        r = self.kb.lookup('副本与 BOSS 速查 暮色森林 女妖圣殿', include_legacy=True)
        self.assertTrue(any(x['review_status'] == 'legacy' for x in r['chunks']))
        for x in r['chunks']:
            if x['review_status'] == 'legacy': self.assertTrue(x['status_note'])
    def test_public_only_filters_everything(self):
        kb = KB(DB, public_only=True); r = kb.lookup('双神怎么打 中桌 顺劈')
        self.assertEqual(r['facts'], [])
        pub = {x[0] for x in c.execute('select alias_norm from aliases where publishable=1')}
        self.assertTrue(all(k in pub for k, v in kb.dict.items() if v['alias']))
        for x in r['chunks']:
            self.assertEqual(c.execute('select publishable from chunks where chunk_id=?', (x['chunk_id'],)).fetchone()[0], 1)
    def test_deterministic_and_budgeted(self):
        a = self.kb.lookup('大海拉P2怎么打'); b = KB(DB).lookup('大海拉P2怎么打')
        self.assertEqual([x['chunk_id'] for x in a['chunks']], [x['chunk_id'] for x in b['chunks']])
        self.assertLessEqual(sum(len(x['text']) for x in a['chunks']), 2400)
    def test_clarify_blocks_retrieval(self):
        for q in ['碎片多少钱', '收碎片', '碎片和魂碎哪个贵']:
            r = self.kb.lookup(q)
            self.assertEqual(r['action'], 'clarify', q)
            self.assertEqual((r['facts'], r['chunks']), ([], []), q)
        self.assertEqual(self.kb.lookup('赐福碎片怎么合成')['action'], 'answer')
    def test_oversized_fact_does_not_block_others(self):
        big = [n for (n,) in c.execute("select name from facts where kind='wiki_equipment' order by length(attrs) desc limit 3")]
        small = ['中级食物桌', '顺劈']
        out = self.kb.facts(big + small, budget=1200)
        self.assertTrue({f['name'] for f in out} & set(small), [f['name'] for f in out])
        self.assertLessEqual(sum(len(f['text']) for f in out), 1200)
        tight = self.kb.facts(big + small, budget=200)          # 大记录装不下 → 跳过，小记录照常返回
        self.assertTrue({f['name'] for f in tight} & set(small))
        self.assertTrue(self.kb._skipped)
    def test_no_internal_ids_in_fact_text(self):
        import re
        for f in self.kb.facts(['逆流长弓', '木镐']):
            self.assertIsNone(re.search(r'\b[a-z_]+:[a-z0-9_]+\b', f['text']), f['text'])
if __name__ == '__main__': unittest.main()
