import asyncio
import copy
import importlib.util
import json
import os
import pathlib
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cloud import unified_shadow as shadow
from cloud.test_trial_plugin_group import FakeEvent, FakeStore, _load_plugin

ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = pathlib.Path(os.environ.get("UNIFIED_CANDIDATE_DIR", ROOT / ".review/unified-r3/stage/unified-r3"))


class CandidateTests(unittest.TestCase):
    def test_expensive_sql_is_interrupted_and_next_query_recovers(self):
        candidate = shadow.Candidate(CANDIDATE)
        try:
            original = candidate.kb.lookup
            def expensive(_question):
                return candidate.kb.c.execute("select count(*) from facts a, facts b, facts c").fetchone()
            candidate.kb.lookup = expensive
            started = time.monotonic()
            with patch.object(shadow, "QUERY_SECONDS", -1), self.assertRaises(sqlite3.OperationalError):
                candidate.lookup("synthetic cost test")
            self.assertLess(time.monotonic() - started, 0.5)
            candidate.kb.lookup = original
            self.assertEqual(candidate.lookup("碎片多少钱")["action"], "clarify")
        finally:
            candidate.close()

    def test_read_only_and_filters_and_clarification(self):
        candidate = shadow.Candidate(CANDIDATE)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                candidate.kb.c.execute("create table must_not_exist(x)")
            for q in ("碎片多少钱", "收碎片", "碎片和魂碎哪个贵"):
                r = candidate.lookup(q)
                self.assertEqual(r["action"], "clarify")
                self.assertEqual((r["facts"], r["chunks"]), ([], []))
                self.assertNotIn("query", r)
            r = candidate.lookup("逆流长弓怎么合成")
            self.assertTrue(r["facts"])
            self.assertLessEqual(sum(len(f["text"]) for f in r["facts"]), 1200)
        finally:
            candidate.close()

    def test_synthetic_rebuilds_have_identical_outputs(self):
        from make_fixture import build_fixture
        with tempfile.TemporaryDirectory() as temp:
            a = shadow.Candidate(build_fixture(pathlib.Path(temp) / "a"))
            b = shadow.Candidate(build_fixture(pathlib.Path(temp) / "b"))
            try:
                for q in ("碎片多少钱", "宝石镶嵌规则", "逆流长弓怎么合成", "合成组合"):
                    self.assertEqual(a.lookup(q), b.lookup(q))
            finally:
                a.close()
                b.close()


class FakeCandidate:
    def __init__(self, _directory):
        pass
    def lookup(self, question):
        if question == "failure secret":
            raise ValueError("must never log this secret")
        return {"action": "clarify", "facts": [], "chunks": [], "clarify_options": [["赐福碎片", "装备碎片", "传说碎片"]]}
    def close(self):
        pass


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.config = {"enabled": True, "run_id": "test-only", "salt": "ab" * 32,
                       "expires_at": time.time() + 3600, "delete_after": time.time() + 7200}
        self.write_config()
        self.worker = None

    def write_config(self):
        (self.root / "unified-shadow-config.json").write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self):
        if self.worker:
            self.worker.close()
        self.temp.cleanup()

    def start(self, cls=FakeCandidate):
        # The background constructor runs during the lifetime of this patch.
        self.patch = patch.object(shadow, "Candidate", cls)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.worker = shadow.Collector(self.root, {"authorized-secret-group"})
        return self.worker

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate())

    def test_privacy_scope_failure_isolation_and_clarify_record(self):
        worker = self.start()
        self.assertFalse(worker.submit("private secret", None, [], []))
        self.assertFalse(worker.submit("outsider secret", "other-group", [], []))
        self.assertFalse(worker.submit("x" * 1025, "authorized-secret-group", [], []))
        self.assertTrue(worker.submit("raw question private nickname", "authorized-secret-group", ["KB numeric data"], []))
        self.assertTrue(worker.submit("failure secret", "authorized-secret-group", [], []))
        self.wait(lambda: worker.stats["processed"] == 1 and worker.stats["errors"] == 1)
        text = (worker.directory / "comparisons.jsonl").read_text(encoding="utf-8")
        for private in ("raw question", "nickname", "authorized-secret-group", "failure secret", "must never log"):
            self.assertNotIn(private, text)
        row = json.loads(text)
        self.assertEqual(row["candidate"]["action"], "clarify")
        self.assertEqual(row["candidate"]["facts"], [])
        self.assertIn("KB numeric data", text)
        if os.name != "nt":
            self.assertEqual((worker.directory / "comparisons.jsonl").stat().st_mode & 0o777, 0o600)

    def test_queue_overload_does_not_wait_for_lookup(self):
        entered, release = threading.Event(), threading.Event()
        class SlowCandidate(FakeCandidate):
            def lookup(self, q):
                entered.set()
                release.wait(timeout=3)
                return super().lookup(q)
        worker = self.start(SlowCandidate)
        try:
            worker.submit("first", "authorized-secret-group", [], [])
            self.assertTrue(entered.wait(timeout=2))
            start = time.monotonic()
            accepted = [worker.submit("next", "authorized-secret-group", [], []) for _ in range(20)]
            self.assertLess(time.monotonic() - start, 0.1)
            self.assertEqual(sum(accepted), shadow.QUEUE_SIZE)
            self.assertEqual(worker.stats["dropped"], 20 - shadow.QUEUE_SIZE)
        finally:
            release.set()

    def test_size_rotation_and_expiration(self):
        self.config["expires_at"] = time.time() - 1
        self.write_config()
        worker = self.start()
        self.assertFalse(worker.submit("expired", "authorized-secret-group", [], []))
        with patch.object(shadow, "LOG_BYTES", 120):
            for n in range(25):
                worker._append({"n": n, "kb": "x" * 70})
            paths = list(worker.directory.glob("comparisons.jsonl*"))
            self.assertEqual(len(paths), shadow.LOG_FILES)
            self.assertTrue(all(p.stat().st_size <= 120 for p in paths))

    def test_retention_removes_comparisons(self):
        self.config["expires_at"] = time.time() - 2
        self.config["delete_after"] = time.time() - 1
        self.write_config()
        directory = self.root / "unified-shadow"
        directory.mkdir()
        (directory / "comparisons.jsonl").write_text("expired synthetic sample", encoding="utf-8")
        worker = self.start()
        self.wait(lambda: not worker.thread.is_alive())
        self.assertFalse((directory / "comparisons.jsonl").exists())


class PromptIsolationTests(unittest.TestCase):
    def test_shadow_results_and_errors_cannot_change_prompt_or_send_messages(self):
        module = _load_plugin()
        plugin = object.__new__(module.NapCatTrial)
        store = FakeStore()
        store.root = pathlib.Path(tempfile.gettempdir())
        module.STORE = store
        with tempfile.TemporaryDirectory() as temp:
            module.PERSONA_PATH = pathlib.Path(temp) / "PERSONA.md"
            module.PERSONA_PATH.write_text("我是奶蛙。", encoding="utf-8")
            results = []
            for behavior in (None, {"action": "clarify", "facts": ["MUST NOT ENTER PROMPT"]}, RuntimeError("synthetic failure")):
                event = FakeEvent({"_napcat_trial_state": {
                    "kind": "group", "group_delegated": True, "chatplus_claimed": True,
                    "question": "碎片多少钱", "group": "synthetic-group", "audit": lambda *a, **kw: None}})
                from types import SimpleNamespace
                req = SimpleNamespace(contexts=[], system_prompt="chatplus", prompt="question", image_urls=[], func_tool=None)
                with patch.object(module.PERSONA_PATH.__class__, "read_text", return_value="我是奶蛙。"), patch.object(module.time, "monotonic", return_value=1):
                    if isinstance(behavior, Exception):
                        fn = lambda *a, **kw: (_ for _ in ()).throw(behavior)
                    else:
                        fn = lambda *a, **kw: behavior
                    module.unified_shadow = SimpleNamespace(submit=fn)
                    # Freeze the wall-clock string in the module via a tiny stub.
                    module.datetime = SimpleNamespace(datetime=SimpleNamespace(now=lambda *_: SimpleNamespace(strftime=lambda *_: "fixed time")))
                    asyncio.run(plugin.prepare_request(event, req))
                self.assertEqual(event.sent, [])
                self.assertFalse(event.stopped)
                results.append(copy.deepcopy(req.__dict__))
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[1], results[2])


if __name__ == "__main__":
    unittest.main()
