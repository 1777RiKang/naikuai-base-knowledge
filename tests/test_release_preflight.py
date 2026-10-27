"""Synthetic fixtures for the offline release preflight; no private knowledge."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))
sys.path.insert(0, str(ROOT / "public/scripts"))
import release_preflight  # noqa: E402
from release_cache import REPOSITORY  # noqa: E402

KB_VERSION = "synthetic-kb-1"
CASE_IDS = ["syn-01", "syn-02"]


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def make_bundle(root, *, version="0.4.0", manifest_version=None, facts=0,
                publishable=1, sensitive=0, kb_version=KB_VERSION,
                repository=REPOSITORY):
    root = Path(root)
    bundle = root / "dist" / "public-r3"
    bundle.mkdir(parents=True)
    (bundle / "README.md").write_text("Synthetic public snapshot.\n", encoding="utf-8")
    (bundle / "LICENSE.md").write_text("Synthetic license note.\n", encoding="utf-8")
    (bundle / "source-manifest.json").write_text(
        json.dumps({"synthetic": True}) + "\n", encoding="utf-8")

    db = bundle / "naikuai-kb-public.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, publishable INT,"
            " sensitive INT, review_status TEXT);"
            "CREATE TABLE facts(id TEXT PRIMARY KEY);"
            "CREATE TABLE aliases(alias TEXT PRIMARY KEY, publishable INT);"
        )
        conn.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            [("schema_version", "unified-r3-public"),
             ("content_scope", "public_clean_only"),
             ("kb_version", kb_version)])
        conn.execute(
            "INSERT INTO chunks(chunk_id,publishable,sensitive,review_status)"
            " VALUES(?,?,?,?)", ("syn-chunk", publishable, sensitive, "active"))
        conn.execute("INSERT INTO aliases(alias,publishable) VALUES(?,?)",
                     ("syn-alias", publishable))
        for index in range(facts):
            conn.execute("INSERT INTO facts(id) VALUES(?)", ("syn-fact-%d" % index,))
        conn.commit()
    finally:
        conn.close()

    cases_path = root / "cases.json"
    cases = {"version": 1, "cases": [
        {"id": case_id, "question": "synthetic " + case_id, "action": "answer"}
        for case_id in CASE_IDS]}
    cases_path.write_text(json.dumps(cases), encoding="utf-8")

    _refresh_manifest(bundle, manifest_version or version, kb_version, repository)

    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    evaluation = {
        "cases": len(CASE_IDS),
        "passed": len(CASE_IDS),
        "database_sha256": _digest(db.read_bytes()),
        "cases_sha256": _digest(cases_path.read_bytes()),
        "results": [{"id": case_id, "passed": True, "errors": []}
                    for case_id in CASE_IDS],
    }
    (root / "evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")
    return {"bundle": bundle, "root": root, "cases": cases_path,
            "evaluation": root / "evaluation.json"}


def _refresh_manifest(bundle, version, kb_version=KB_VERSION, repository=REPOSITORY):
    bundle = Path(bundle)
    names = ["naikuai-kb-public.sqlite", "README.md", "LICENSE.md", "source-manifest.json"]
    manifest = {
        "schema_version": "unified-r3-public",
        "kb_version": kb_version,
        "release_version": version,
        "repository": repository,
        "files": [{"path": name,
                   "size_bytes": (bundle / name).stat().st_size,
                   "sha256": _digest((bundle / name).read_bytes())}
                  for name in names],
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ReleasePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="naikuai-preflight-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_preflight(self, *, output=None):
        return release_preflight.run_preflight(
            self.root / "dist" / "public-r3",
            self.root / "evaluation.json",
            output=output,
            version_path=self.root / "VERSION",
            cases_path=self.root / "cases.json")

    def failures(self, report):
        return {item["check"]: item["reason"] for item in report["failures"]}

    def test_synthetic_bundle_passes(self):
        make_bundle(self.root)
        report = self.run_preflight()
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["version"], "0.4.0")
        self.assertEqual(report["database"]["facts"], 0)
        self.assertEqual(report["database"]["chunks"], 1)
        self.assertEqual(report["evaluation_result"]["passed"], 2)
        self.assertEqual(report["layout"]["entries"], [
            "LICENSE.md", "README.md", "manifest.json",
            "naikuai-kb-public.sqlite", "source-manifest.json"])
        self.assertEqual(report["evaluation_result"]["database_sha256"],
                         report["database"]["sha256"])
        self.assertTrue(report["manifest_sha256"])

    def test_output_file_matches_report(self):
        make_bundle(self.root)
        output = self.root / "evidence.json"
        report = self.run_preflight(output=output)
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)

    def test_rejects_version_mismatch(self):
        make_bundle(self.root, manifest_version="9.9.9")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("version-match", self.failures(report))

    def test_rejects_tampered_asset(self):
        make_bundle(self.root)
        (self.root / "dist" / "public-r3" / "README.md").write_text(
            "synthetic corruption\n", encoding="utf-8")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("assets", self.failures(report))

    def test_rejects_extra_file_in_bundle(self):
        make_bundle(self.root)
        (self.root / "dist" / "public-r3" / "extra-private.sqlite").write_bytes(b"private")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("unexpected release entries: extra-private.sqlite",
                      self.failures(report)["bundle-layout"])

    def test_rejects_extra_directory_in_bundle(self):
        make_bundle(self.root)
        nested = self.root / "dist" / "public-r3" / "nested"
        nested.mkdir()
        (nested / "payload.txt").write_text("synthetic\n", encoding="utf-8")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("unexpected release entries: nested",
                      self.failures(report)["bundle-layout"])

    def test_rejects_symlinked_asset(self):
        make_bundle(self.root)
        bundle = self.root / "dist" / "public-r3"
        outside = self.root / "outside.md"
        outside.write_bytes((bundle / "README.md").read_bytes())
        (bundle / "README.md").unlink()
        try:
            (bundle / "README.md").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation needs privileges on this platform")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("README.md is a symlink", self.failures(report)["bundle-layout"])

    def test_rejects_private_facts(self):
        make_bundle(self.root, facts=1)
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("public-database", self.failures(report))

    def test_rejects_non_publishable_chunk(self):
        make_bundle(self.root, publishable=0)
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("not publishable", self.failures(report)["public-database"])

    def test_rejects_missing_evaluation(self):
        make_bundle(self.root)
        (self.root / "evaluation.json").unlink()
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("evaluation", self.failures(report))

    def test_rejects_incomplete_evaluation(self):
        make_bundle(self.root)
        evaluation = json.loads((self.root / "evaluation.json").read_text(encoding="utf-8"))
        evaluation["results"] = evaluation["results"][:1]
        (self.root / "evaluation.json").write_text(
            json.dumps(evaluation), encoding="utf-8")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("of 2 cases", self.failures(report)["evaluation"])

    def test_rejects_failed_evaluation_case(self):
        make_bundle(self.root)
        evaluation = json.loads((self.root / "evaluation.json").read_text(encoding="utf-8"))
        evaluation["results"][0]["passed"] = False
        evaluation["passed"] = 1
        (self.root / "evaluation.json").write_text(
            json.dumps(evaluation), encoding="utf-8")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("failed evaluation cases", self.failures(report)["evaluation"])

    def test_rejects_wrong_repository(self):
        make_bundle(self.root, repository="example.invalid/other-repo")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("manifest-contract", self.failures(report))

    def test_rejects_unbound_legacy_evaluation(self):
        make_bundle(self.root)
        evaluation = json.loads((self.root / "evaluation.json").read_text(encoding="utf-8"))
        del evaluation["database_sha256"]
        del evaluation["cases_sha256"]
        (self.root / "evaluation.json").write_text(
            json.dumps(evaluation), encoding="utf-8")
        report = self.run_preflight()
        self.assertFalse(report["ok"])
        self.assertIn("lacks database_sha256/cases_sha256 binding",
                      self.failures(report)["evaluation"])

    def test_rejects_evaluation_bound_to_another_database(self):
        make_bundle(self.root)
        bundle = self.root / "dist" / "public-r3"
        conn = sqlite3.connect(bundle / "naikuai-kb-public.sqlite")
        try:
            conn.execute("INSERT INTO aliases(alias,publishable) VALUES(?,?)",
                         ("syn-alias-2", 1))
            conn.commit()
        finally:
            conn.close()
        _refresh_manifest(bundle, "0.4.0")
        report = self.run_preflight()
        failures = self.failures(report)
        self.assertNotIn("assets", failures)
        self.assertIn("database_sha256 does not match",
                      failures["evaluation"])

    def test_rejects_evaluation_bound_to_another_case_set(self):
        make_bundle(self.root)
        cases = json.loads((self.root / "cases.json").read_text(encoding="utf-8"))
        cases["cases"][0]["question"] += " changed"
        (self.root / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
        report = self.run_preflight()
        self.assertIn("cases_sha256 does not match",
                      self.failures(report)["evaluation"])


if __name__ == "__main__":
    unittest.main()
