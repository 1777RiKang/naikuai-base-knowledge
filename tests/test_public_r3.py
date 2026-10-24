"""Run the reviewed public snapshot checks in the normal repository test suite."""
import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicR3Tests(unittest.TestCase):
    def test_public_source_manifest(self):
        public = ROOT / "public"
        manifest = json.loads((public / "source-manifest.json").read_text(encoding="utf-8"))
        for item in manifest["files"]:
            with self.subTest(path=item["path"]):
                path = (public / item["path"]).resolve()
                self.assertTrue(path.is_relative_to(public.resolve()))
                self.assertNotEqual(path.suffix, ".sqlite")
                self.assertEqual(path.stat().st_size, item["size_bytes"])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])

    def test_reviewed_public_snapshot(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "build/verify_public_r3.py")],
                                cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-3000:])
