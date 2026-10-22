# -*- coding: utf-8 -*-
import copy
import sqlite3
import sys
import tempfile
import unittest
import json
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

import build_sqlite  # noqa: E402
import validate  # noqa: E402
from common import load_terms  # noqa: E402
from common import norm, UniqueKeyLoader
from privacy import term_privacy_errors, contains_secret
from query import search
import yaml


class GlossaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = load_terms()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.manifest = build_sqlite.build(Path(cls.tmp.name))
        cls.db = Path(cls.tmp.name) / "glossary.sqlite"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def query(self, sql, args=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def test_no_validation_errors(self):
        self.assertEqual(validate.collect_errors(self.docs), [])

    def test_entry_count(self):
        self.assertGreater(len(self.docs), 0)
        self.assertEqual(self.manifest["counts"]["entries"], len(self.docs))
        self.assertEqual(self.query("SELECT COUNT(*) FROM entries")[0][0], len(self.docs))

    def test_export_preserves_complete_documents(self):
        rows = dict(self.query("SELECT entry_id, document_json FROM entry_metadata"))
        for _, doc in self.docs:
            self.assertEqual(json.loads(rows[doc['id']]), doc)
        self.assertEqual(self.manifest['data_license'], 'CC-BY-NC-SA-4.0')

    def test_content_search_and_status_filter(self):
        self.assertIn('material.jiujing', [d['id'] for d in search(self.db, '燃料', limit=50)])
        self.assertIn('material.tiekuai', [d['id'] for d in search(self.db, '铁块', limit=50)])

    def test_status_filter_excludes_draft(self):
        docs = [(path, copy.deepcopy(doc)) for path, doc in self.docs]
        target = next(doc for _, doc in docs if doc['id'] == 'material.tiekuai')
        target['review'] = {'status': 'draft', 'reviewer': None, 'reviewed_at': None}
        with tempfile.TemporaryDirectory() as temporary, patch.object(build_sqlite, 'load_terms', return_value=docs):
            build_sqlite.build(Path(temporary))
            db = Path(temporary) / 'glossary.sqlite'
            rows = search(db, '铁块', limit=50)
            self.assertNotIn('material.tiekuai', [d['id'] for d in rows])
            all_rows = search(db, '铁块', include_unconfirmed=True, limit=50)
            self.assertIn('material.tiekuai', [d['id'] for d in all_rows])

    def test_normalization_matches_validation(self):
        docs = [(path, copy.deepcopy(doc)) for path, doc in self.docs]
        target = next(doc for _, doc in docs if doc['id'] == 'item.heizhang')
        target.setdefault('aliases', []).append('Ａ B')
        with tempfile.TemporaryDirectory() as temporary, patch.object(build_sqlite, 'load_terms', return_value=docs):
            build_sqlite.build(Path(temporary))
            db = sqlite3.connect(Path(temporary) / 'glossary.sqlite')
            self.assertEqual(db.execute('SELECT alias_norm FROM entry_aliases WHERE alias=?', ('Ａ B',)).fetchone()[0], norm('Ａ B'))
            db.close()

    def test_privacy_checks_all_sources_and_fields(self):
        for source in ('community', 'in-game'):
            doc = {'provenance': {'source': source}, 'senses': [{'notes': '9' * 8}]}
            errors = term_privacy_errors(doc, 'example.yaml')
            self.assertTrue(errors)
            self.assertNotIn('9' * 8, ' '.join(errors))
        self.assertTrue(contains_secret('gh' + 'p_' + 'a' * 36))

    def test_duplicate_yaml_keys_rejected(self):
        with self.assertRaises(yaml.constructor.ConstructorError):
            yaml.load('term: a\nterm: b\n', Loader=UniqueKeyLoader)

    def test_foreign_keys_and_confirmed_view(self):
        self.assertEqual(self.query('PRAGMA foreign_key_check'), [])
        self.assertEqual(self.query('SELECT COUNT(*) FROM ai_entries')[0][0],
                         sum(d['review']['status'] == 'confirmed' for _, d in self.docs))

    def test_alias_conflict_detected(self):
        docs = [(path, copy.deepcopy(doc)) for path, doc in self.docs]
        target = next(doc for _, doc in docs if doc["id"] == "item.heizhang")
        target["aliases"] = ["黑奶"]
        errors = validate.collect_errors(docs)
        self.assertTrue(any("别名冲突" in err for err in errors), errors)

    def test_alias_resolves(self):
        rows = self.query(
            "SELECT e.id FROM entry_aliases a JOIN entries e ON e.id = a.entry_id WHERE a.alias = ?",
            ("黑仗",),
        )
        self.assertEqual(rows, [("item.heizhang",)])

    def test_variant_relation(self):
        rows = self.query("SELECT target FROM relations WHERE entry_id = ? AND kind = ?",
                          ("item.heinai2", "variant_of"))
        self.assertEqual(rows, [("item.heizhang",)])

    def test_composite_components_exist(self):
        rows = self.query("SELECT target FROM relations WHERE entry_id = ? AND kind = 'composes'",
                          ("composite.jingling-feixing",))
        targets = {r[0] for r in rows}
        self.assertEqual(targets, {"material.jinglingshi", "item.feixing-qiyue"})
        for target in targets:
            self.assertEqual(self.query("SELECT COUNT(*) FROM entries WHERE id = ?", (target,))[0][0], 1)

    def test_token_index(self):
        rows = self.query("SELECT DISTINCT ref FROM terms WHERE token = ?", ("黑奶",))
        self.assertIn(("item.heinai2",), rows)

    def test_integrity(self):
        self.assertEqual(self.query("PRAGMA integrity_check")[0][0], "ok")


if __name__ == "__main__":
    unittest.main()
