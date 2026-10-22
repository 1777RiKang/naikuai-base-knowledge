"""Authorized full-text publication, attribution, review and retrieval regressions."""
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
import import_sources
from common import ROOT
from knowledge import collect_knowledge_errors, digest, load_knowledge, redact


def fixture(entry_id='knowledge.author-summary-test',body='测试装备，提供火焰防护。'):
    return import_sources.make_record(entry_id,'测试装备','equipment','测试章节',
                                     '2026-10-22T00:00:00+00:00',body)


def docs_for(record):
    return [(Path('equipment')/(record['id'].split('.',1)[1]+'.json'),record)]


class KnowledgeTest(unittest.TestCase):
    def test_published_chapters_are_author_content_and_not_fake_confirmations(self):
        docs=load_knowledge()
        self.assertEqual(len(docs),387)
        self.assertEqual(collect_knowledge_errors(docs),[])
        for _,doc in docs:
            self.assertTrue(doc['content'].strip())
            self.assertFalse(doc['credit_eligible'])
            self.assertEqual(doc['provenance']['source'],'author')
            self.assertEqual(doc['provenance']['acquired_via'],'local-file')
            self.assertEqual(doc['provenance']['publication'],'fulltext')
        self.assertEqual(fixture()['review']['status'],'draft')

    def test_wrong_attribution_content_hash_and_contacts_rejected(self):
        author=fixture()
        self.assertEqual(author['provenance']['attribution'],'乱走丶沙城')
        author['provenance']['attribution']='other'
        self.assertTrue(collect_knowledge_errors(docs_for(author)))
        changed=fixture()
        changed['content']='未经同步的新内容'
        self.assertTrue(collect_knowledge_errors(docs_for(changed)))
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

    def test_public_fulltext_build_and_review_filter(self):
        record=fixture()
        with tempfile.TemporaryDirectory() as tmp,patch.object(build_knowledge,'validate_catalog',return_value=docs_for(record)):
            root=Path(tmp)
            manifest=build_knowledge.build(root)
            self.assertEqual(manifest['content_scope'],'public-fulltext')
            self.assertGreater(manifest['counts']['chunks'],0)
            self.assertEqual(build_knowledge.search(root/'knowledge.sqlite','测试装备'),[])
            result=build_knowledge.search(root/'knowledge.sqlite','测试装备',include_unconfirmed=True)
            self.assertEqual(result[0]['provenance']['attribution'],'乱走丶沙城')
            self.assertIn('火焰',result[0]['excerpt'])
            self.assertTrue(result[0]['retrieval']['body_available'])
            self.assertEqual(result[0]['content'],record['content'])
            self.assertTrue((root/'data-summary/full.md').is_file())

    def test_changed_body_resets_review_unchanged_preserves_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            record=fixture()
            record['review']={'status':'confirmed','reviewer':'maintainer','reviewed_at':'2026-10-22'}
            import_sources.write_snapshot([copy.deepcopy(record)],{},root/'knowledge')
            same=fixture()
            rows=import_sources.write_snapshot([same],{},root/'knowledge')
            self.assertEqual(rows[0][1]['review']['status'],'confirmed')
            rows=import_sources.write_snapshot([fixture(body='新内容')],{},root/'knowledge')
            self.assertEqual(rows[0][1]['review']['status'],'draft')

    def test_duplicate_pages_return_one_result(self):
        first=fixture()
        second=fixture(entry_id='knowledge.author-summary-copy')
        second['duplicate_of']=first['id']
        docs=docs_for(first)+docs_for(second)
        with tempfile.TemporaryDirectory() as tmp,patch.object(build_knowledge,'validate_catalog',return_value=docs):
            build_knowledge.build(Path(tmp))
            result=build_knowledge.search(Path(tmp)/'knowledge.sqlite','测试装备',include_unconfirmed=True)
            self.assertEqual(len(result),1)

    def test_body_hash_and_invalid_duplicate_links_are_rejected(self):
        record=fixture()
        record['duplicate_of']=record['id']
        self.assertTrue(collect_knowledge_errors(docs_for(record)))
        del record['duplicate_of']
        record['provenance']['content_sha256']=digest('旧内容')
        with tempfile.TemporaryDirectory() as tmp,patch.object(build_knowledge,'validate_catalog',return_value=docs_for(record)):
            with self.assertRaises(ValueError):
                build_knowledge.build(Path(tmp))

    def test_all_source_images_and_fulltext_hash_are_available(self):
        base=ROOT/'sources/data-summary'
        manifest=json.loads((base/'extraction-manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256((base/'full.md').read_bytes()).hexdigest(),manifest['published_markdown_sha256'])
        self.assertEqual(manifest['tables'],372)
        self.assertEqual(manifest['image_references'],41)
        for name,expected in manifest['images'].items():
            self.assertEqual(hashlib.sha256((base/'images'/name).read_bytes()).hexdigest(),expected)
        self.assertNotIn('data:image/',(base/'full.md').read_text(encoding='utf-8'))

    def test_reimport_needs_only_committed_summary_and_keeps_every_section(self):
        records,receipt=import_sources.collect_snapshot(ROOT/'sources/data-summary/full.md')
        current={doc['id']:doc for _,doc in load_knowledge()}
        self.assertEqual(set(current),{doc['id'] for doc in records})
        for doc in records:
            self.assertEqual(doc['content'],current[doc['id']]['content'])
        self.assertEqual(receipt['counts']['knowledge_entries'],387)


if __name__=='__main__':
    unittest.main()
