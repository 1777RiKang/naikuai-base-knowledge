"""Keep the optional runtime example independent from the public glossary."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QQBotExampleTests(unittest.TestCase):
    def test_synthetic_integration_suite(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "examples/qq_bot_r3/run_tests.py")],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-5000:])
