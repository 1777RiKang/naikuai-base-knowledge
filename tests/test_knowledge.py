"""Regression tests for public metadata, private bodies, updates and attribution."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'build'))
import build_knowledge
from common import load_terms
import import_sources
from knowledge import collect_knowledge_errors, digest, load_knowledge, redact


def fixture(entry_id='knowledge.nkwiki-equipment-1', source='nkwiki',body='测试装备，提供火焰防护。'):
    return import_sources.make_record(entry_id,'测试装备','equipment',source,
        'nkwiki','equipment:1','2026-10-22T00:00:00+00:00',None,body)


class KnowledgeTest(unittest.TestCase):
    def test_catalog_has_no_bodies_or_fake_confirmations(self):
        docs=load_knowledge()
        self.assertTrue(docs)
        self.assertEqual(collect_knowledge_errors(docs),[])
        for _,doc in docs:
            self.assertFalse(doc['credit_eligible'])
            self.assertNotIn('content',doc)
            self.assertNotIn('text',doc)
        record,_=fixture()
        record['content']='不能发布的全文'
        self.assertTrue(collect_knowledge_errors([(Path('equipment/nkwiki-equipment-1.json'),record)]))

    def test_source_attribution_and_privacy(self):
        author,_=fixture(source='author')
        self.assertEqual(author['provenance']['attribution'],'乱走丶沙城')
        author['provenance']['attribution']='nkwiki'
        self.assertTrue(collect_knowledge_errors([(Path('equipment/nkwiki-equipment-1.json'),author)]))
        private='联系'+'Q群：'+('9'*8)
        cleaned=redact(private+'；游戏伤害：'+'1'+'0'*7)
        self.assertNotIn('9'*8,cleaned)
        self.assertIn('1'+'0'*7,cleaned)
        self.assertNotIn('gh'+'p_'+'a'*36,redact('gh'+'p_'+'a'*36))

    def test_summary_owns_its_sections_and_has_stable_ids(self):
        text='# 第一章 基础数据\n简介\n## 1.1 属性\n属性说明\n### 1.1.1 生命\n生命数据\n'
        parts=list(import_sources.split_summary(text))
        self.assertEqual(len(parts),3)
        self.assertNotIn('生命数据',parts[0]['body'])
        changed=list(import_sources.split_summary(text.replace('生命数据','更新生命数据')))
        self.assertEqual([p['id'] for p in parts],[p['id'] for p in changed])
        is_author,rule=import_sources.is_summary_article({'categoryName':'第二章 PVE数据总结','content':'内容'},'')
        self.assertTrue(is_author)

    def test_public_build_cannot_include_private_bodies(self):
        record,body=fixture()
        docs=[(Path('equipment/nkwiki-equipment-1.json'),record)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            local=root/'.local'
            local.mkdir()
            path=local/'bodies.jsonl'
            path.write_text(json.dumps({'id':record['id'],'content':body,'content_sha256':digest(body)},ensure_ascii=False)+'\n',encoding='utf-8')
            with patch.object(build_knowledge,'ROOT',root),patch.object(build_knowledge,'validate_catalog',return_value=docs):
                with self.assertRaises(ValueError):
                    build_knowledge.build(root/'dist',path)
                public=build_knowledge.build(root/'dist')
                self.assertEqual(public['content_scope'],'metadata-only')
                self.assertEqual(public['counts']['chunks'],0)
                private=build_knowledge.build(local,path)
                self.assertEqual(private['content_scope'],'local-fulltext')
                self.assertGreater(private['counts']['chunks'],0)
                self.assertEqual(build_knowledge.search(local/'knowledge.sqlite','测试装备'),[])
                result=build_knowledge.search(local/'knowledge.sqlite','测试装备',include_unconfirmed=True)
                self.assertEqual(result[0]['provenance']['attribution'],'nkwiki')
                self.assertIn('火焰',result[0]['excerpt'])
                self.assertTrue(result[0]['retrieval']['body_available'])
                self.assertFalse(build_knowledge.search(root/'dist/knowledge.sqlite','测试装备',include_unconfirmed=True)[0]['retrieval']['body_available'])

    def test_changed_body_resets_review_unchanged_body_preserves_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            local=root/'.local'
            record,body=fixture()
            record['review']={'status':'confirmed','reviewer':'maintainer','reviewed_at':'2026-10-22'}
            with patch.object(import_sources,'ROOT',root):
                import_sources.write_snapshot([copy.deepcopy(record)],{}, {},root/'knowledge',local)
                same=copy.deepcopy(record)
                same['review']={'status':'draft','reviewer':None,'reviewed_at':None}
                rows=import_sources.write_snapshot([same],{}, {},root/'knowledge',local)
                self.assertEqual(rows[0][1]['review']['status'],'confirmed')
                updated=copy.deepcopy(record)
                updated['provenance']['content_sha256']=digest('新内容')
                updated['review']={'status':'draft','reviewer':None,'reviewed_at':None}
                rows=import_sources.write_snapshot([updated],{}, {},root/'knowledge',local)
                self.assertEqual(rows[0][1]['review']['status'],'draft')

    def test_duplicate_pages_return_one_result(self):
        first,body=fixture()
        second=copy.deepcopy(first)
        second['id']='knowledge.nkwiki-equipment-2'
        second['duplicate_of']=first['id']
        docs=[(Path('equipment/nkwiki-equipment-1.json'),first),(Path('equipment/nkwiki-equipment-2.json'),second)]
        with tempfile.TemporaryDirectory() as tmp,patch.object(build_knowledge,'validate_catalog',return_value=docs):
            build_knowledge.build(Path(tmp))
            result=build_knowledge.search(Path(tmp)/'knowledge.sqlite','测试装备',include_unconfirmed=True)
            self.assertEqual(len(result),1)

    def test_bad_duplicate_links_and_stale_local_bodies_are_rejected(self):
        record,body=fixture()
        record['duplicate_of']=record['id']
        self.assertTrue(collect_knowledge_errors([(Path('equipment/nkwiki-equipment-1.json'),record)]))
        del record['duplicate_of']
        docs=[(Path('equipment/nkwiki-equipment-1.json'),record)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            local=root/'.local'
            local.mkdir()
            path=local/'bodies.jsonl'
            path.write_text(json.dumps({'id':record['id'],'content':'旧内容','content_sha256':digest('旧内容')})+'\n',encoding='utf-8')
            with patch.object(build_knowledge,'ROOT',root),patch.object(build_knowledge,'validate_catalog',return_value=docs):
                with self.assertRaises(ValueError):
                    build_knowledge.build(local,path)



if __name__=='__main__':
    unittest.main()
