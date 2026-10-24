import asyncio
import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from cloud.test_trial_plugin_group import FakeEvent, FakeStore, _load_plugin

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cloud"))
DB_ROOT = pathlib.Path(os.environ.get("UNIFIED_CANDIDATE_DIR", ROOT / ".review/unified-r3/stage/unified-r3")).parent


class PrivateKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_plugin()
        self.plugin = object.__new__(self.module.NapCatTrial)
        self.store = FakeStore()
        self.module.STORE = self.store
        self.temp = tempfile.TemporaryDirectory()
        self.persona = pathlib.Path(self.temp.name) / "PERSONA.md"
        self.persona.write_text("我是奶蛙。", encoding="utf-8")
        self.module.PERSONA_PATH = self.persona
        self.audit = []

    def tearDown(self):
        self.temp.cleanup()

    def request(self, kind="private", question="宝石镶嵌规则"):
        state = {"kind": kind, "key": "synthetic-private", "question": question,
                 "group": None if kind == "private" else "synthetic-group",
                 "images": ["base64://synthetic"], "audit": lambda *a, **kw: self.audit.append((a, kw))}
        if kind == "group":
            state.update(group_delegated=True, chatplus_claimed=True)
        event = FakeEvent({"_napcat_trial_state": state})
        req = types.SimpleNamespace(contexts=[], system_prompt="chatplus rules", prompt="question",
                                    image_urls=[], conversation="native", func_tool=["unsafe"])
        return state, event, req

    def test_private_uses_new_material_and_retains_memory_images_without_old_kbs(self):
        self.module.unified_private = types.SimpleNamespace(
            lookup=lambda *args: {"action": "answer", "reply": "", "material": "【攻略资料】\nsynthetic-r3-evidence"},
            RULES="new private knowledge rules",
        )
        state, event, req = self.request()
        asyncio.run(self.plugin.prepare_request(event, req))
        self.assertFalse(event.stopped)
        self.assertEqual(event.sent, [])
        self.assertIn("synthetic-r3-evidence", req.system_prompt)
        self.assertIn("new private knowledge rules", req.system_prompt)
        self.assertEqual(req.contexts[0]["content"], "private history")
        self.assertEqual(req.image_urls, ["base64://synthetic"])
        self.assertEqual(self.store.lookups, [])
        self.assertEqual(len(self.store.prepare_calls), 1)
        self.assertTrue(state["kb_used"])
        self.assertEqual(state["kb_backend"], "unified_r3")
        self.assertIsNone(req.func_tool)
        self.assertEqual(self.audit[-1][1]["reason"], "private_unified")

    def test_clarify_stops_before_memory_and_provider(self):
        self.module.unified_private = types.SimpleNamespace(
            lookup=lambda *args: {"action": "clarify", "reply": "你说的是赐福碎片、装备碎片还是传说碎片？", "material": ""})
        state, event, req = self.request(question="碎片多少钱")
        provider_calls = []
        async def pipeline():
            await self.plugin.prepare_request(event, req)
            if not event.stopped:
                provider_calls.append(req)
        asyncio.run(pipeline())
        self.assertEqual(provider_calls, [])
        self.assertTrue(state["local_command"])
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(self.store.prepare_calls, [])
        self.assertEqual(self.audit[-1][1]["reason"], "private_unified_clarify")

    def test_failure_stops_without_stale_data_or_model_fallback(self):
        def fail(*args):
            raise RuntimeError("private error must not be sent")
        self.module.unified_private = types.SimpleNamespace(lookup=fail)
        state, event, req = self.request()
        asyncio.run(self.plugin.prepare_request(event, req))
        self.assertTrue(event.stopped)
        self.assertEqual(event.sent, ["测试知识库暂时无法读取，请稍后再试。"])
        self.assertEqual(self.store.prepare_calls, [])
        self.assertEqual(self.store.lookups, [])

    def test_group_never_calls_private_database(self):
        def fail(*args):
            raise AssertionError("private database called in group")
        self.module.unified_private = types.SimpleNamespace(lookup=fail)
        state, event, req = self.request(kind="group")
        asyncio.run(self.plugin.prepare_request(event, req))
        self.assertFalse(event.stopped)
        self.assertNotIn("kb_backend", state)
        self.assertIn("wiki record", req.system_prompt)
        self.assertNotIn("统一知识库 r3", req.system_prompt)

    def test_empty_private_retrieval_keeps_normal_chat(self):
        self.module.unified_private = types.SimpleNamespace(
            lookup=lambda *args: {"action": "answer", "material": "", "reply": ""}, RULES="private rules")
        state, event, req = self.request(question="今天有点累")
        asyncio.run(self.plugin.prepare_request(event, req))
        self.assertFalse(event.stopped)
        self.assertFalse(state["kb_used"])
        self.assertEqual(req.prompt, "今天有点累")


class RealDatabaseTests(unittest.TestCase):
    def test_real_gem_material_and_ambiguity_output(self):
        # This imports the same stdlib-only wrapper uploaded to the VPS.
        spec = importlib.util.spec_from_file_location("private_kb_test", ROOT / "cloud/unified_private.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.lookup(DB_ROOT, "宝石镶嵌规则")
        self.assertEqual(result["action"], "answer")
        self.assertIn("装备孔位", result["material"])
        self.assertIn("未核验", result["material"])
        for q in ("碎片多少钱", "收碎片", "碎片和魂碎哪个贵"):
            result = module.lookup(DB_ROOT, q)
            self.assertEqual(result["action"], "clarify")
            self.assertEqual(result["material"], "")
            self.assertIn("赐福碎片", result["reply"])


if __name__ == "__main__":
    unittest.main()
