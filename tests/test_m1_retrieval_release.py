"""Natural questions and release staging boundaries, without network or production data."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'public/scripts'))
sys.path.insert(0, str(ROOT / 'build'))
from release_cache import ReleaseLayer, stage_release
from evaluate_public_r3 import evaluate
from stage_public_release import manifest_base


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class M1ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='naikuai-m1-tests-')
        cls.root = Path(cls.temp.name)
        cls.bundle = cls.root / 'bundle'
        builder = load(ROOT / 'build/build_public_r3.py', 'artifact_builder_test')
        with contextlib.redirect_stdout(io.StringIO()):
            builder.build(cls.bundle)
        cls.manifest = (cls.bundle / 'manifest.json').read_bytes()
        cls.digest = hashlib.sha256(cls.manifest).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def fetch(self, name, limit):
        data = (self.bundle / name).read_bytes()
        self.assertLessEqual(len(data), limit)
        return data

    def test_fixed_natural_questions(self):
        report = evaluate(self.bundle / 'naikuai-kb-public.sqlite')
        self.assertEqual(report['cases'], 30)
        self.assertEqual(report['passed'], report['cases'], [r for r in report['results'] if not r['passed']])

    def test_release_staging_and_private_fallback(self):
        target = stage_release(self.manifest, self.digest, self.fetch, self.root / 'cache-good')
        reader = ReleaseLayer(target, self.digest)
        private = {'action':'answer', 'facts':[{'kind':'gem_ref','text':'synthetic private-only evidence'}], 'chunks':[]}
        try:
            self.assertEqual(reader.lookup('宝石镶嵌规则', lambda _: private), private)
            called = []
            result = reader.lookup('宝石怎么打', lambda _: called.append(True))
            self.assertEqual(result['action'], 'clarify')
            self.assertEqual(called, [])
            self.assertEqual(reader.lookup('海拉15人怎么玩')['action'], 'clarify')
            exact = reader.lookup('合成私有单品数值', lambda _: private)
            self.assertEqual(exact['facts'], private['facts'])
            job = reader.lookup('猎魔人怎么玩', lambda _: private)
            self.assertFalse(job['facts'])
            self.assertIn('2.3.3.3猎魔人', job['chunks'][0]['section'])
        finally:
            reader.close()
        self.assertEqual(stage_release(self.manifest, self.digest, self.fetch, self.root/'cache-good'), target)

    def test_bad_download_never_touches_private_database(self):
        private = self.root / 'synthetic-private.sqlite'
        private.write_bytes(b'synthetic-private-database-placeholder')
        before = private.read_bytes()
        cache = self.root / 'cache-bad'
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            stage_release(self.manifest, self.digest, lambda *args:b'corrupted', cache)
        self.assertEqual(private.read_bytes(), before)
        self.assertEqual(list(cache.iterdir()), [])

    def test_asset_path_traversal_is_rejected_before_download(self):
        manifest = json.loads(self.manifest)
        manifest['files'][0]['path'] = '../synthetic-outside.sqlite'
        data = json.dumps(manifest).encode()
        called = []
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            stage_release(data, hashlib.sha256(data).hexdigest(), lambda *a:called.append(True), self.root/'cache-traversal')
        self.assertEqual(called, [])

    def test_cached_asset_tampering_fails_closed(self):
        target = stage_release(self.manifest, self.digest, self.fetch, self.root/'cache-tamper')
        (target/'README.md').write_text('synthetic corruption', encoding='utf-8')
        with self.assertRaises(ValueError):
            ReleaseLayer(target, self.digest)

    def test_release_origin_is_fixed(self):
        valid = 'https://github.com/1777RiKang/naikuai-base-knowledge/releases/download/v0.4.0/manifest.json'
        self.assertTrue(manifest_base(valid).endswith('/v0.4.0'))
        for invalid in ['http://127.0.0.1/manifest.json', valid.replace('github.com','example.invalid'), valid+'?redirect=1']:
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                manifest_base(invalid)

    def test_profession_does_not_return_same_named_npc_in_full_reader(self):
        # An invented NPC fixture proves the boundary despite the public facts=0.
        db = self.root/'same-name-fixture.sqlite'
        conn = sqlite3.connect(db)
        conn.executescript((ROOT/'examples/qq_bot_r3/unified_r3/schema.sql').read_text(encoding='utf-8'))
        conn.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     ('synthetic-npc','npc','猎魔人','猎魔人','synthetic','fixture','synthetic',1,'[]','{"说明":"synthetic npc only"}',0))
        conn.commit();conn.close()
        module = load(ROOT/'examples/qq_bot_r3/unified_r3/kb_query.py', 'full_reader_fixture')
        kb = module.KB(str(db))
        try:
            self.assertEqual(len(kb.lookup('猎魔人')['facts']), 1)
            self.assertEqual(len(kb.lookup('猎魔人NPC技能')['facts']), 1)
            self.assertEqual(kb.lookup('猎魔人怎么玩')['facts'], [])
            self.assertEqual(kb.lookup('猎魔人如何玩')['facts'], [])
        finally:
            kb.c.close()
