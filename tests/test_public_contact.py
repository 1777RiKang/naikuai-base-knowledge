"""Keep the authorized author contact exception confined to its exact notice."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'build'))
import privacy


class PublicContactTest(unittest.TestCase):
    def scan(self, rel, content):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
            listing = SimpleNamespace(stdout=rel.encode('utf-8') + b'\0')
            with patch.object(privacy.subprocess, 'run', return_value=listing):
                return privacy.scan_repository(root)

    def test_exact_author_notice_is_allowed(self):
        self.assertEqual(self.scan(privacy.PUBLIC_CONTACT_PATH, privacy.PUBLIC_CONTACT_LINE), [])

    def test_contact_in_another_file_is_rejected(self):
        self.assertTrue(self.scan('terms/contact.yaml', privacy.PUBLIC_CONTACT_LINE))

    def test_added_contact_is_rejected(self):
        extra = ' QQ:' + '12345' + '67890'
        self.assertTrue(self.scan(privacy.PUBLIC_CONTACT_PATH, privacy.PUBLIC_CONTACT_LINE + extra))

    def test_credentials_in_author_document_are_rejected(self):
        secret = 'ghp_' + 'a' * 30
        self.assertTrue(self.scan(privacy.PUBLIC_CONTACT_PATH, privacy.PUBLIC_CONTACT_LINE + '\n' + secret))
