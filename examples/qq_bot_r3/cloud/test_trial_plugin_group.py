import asyncio
import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "cloud" / "napcat_trial_plugin" / "main.py"


def _decorator(*args, **kwargs):
    def decorate(fn):
        fn._test_priority = kwargs.get("priority", 0)
        return fn
    return decorate


def _load_plugin():
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = type("AstrMessageEvent", (), {})
    event.filter = types.SimpleNamespace(
        event_message_type=_decorator,
        on_llm_request=_decorator,
        on_llm_response=_decorator,
        after_message_sent=_decorator,
        EventMessageType=types.SimpleNamespace(ALL=object(), GROUP_MESSAGE=object()),
    )
    star = types.ModuleType("astrbot.api.star")
    star.Star = type("Star", (), {"__init__": lambda self, context: None})
    star.register = _decorator
    with patch.dict(sys.modules, {"astrbot": types.ModuleType("astrbot"), "astrbot.api": api,
                                  "astrbot.api.event": event, "astrbot.api.star": star}):
        if str(ROOT / "cloud") not in sys.path:
            sys.path.insert(0, str(ROOT / "cloud"))
        with patch.dict(os.environ, {"ASTRBOT_ROOT": tempfile.gettempdir()}):
            source = PLUGIN.read_text(encoding="utf-8")
            source = source.replace(
                'if ROOT != pathlib.Path("/var/lib/astrbot-naiwa-trial"):\n'
                '    raise RuntimeError("NapCat trial plugin loaded outside its isolated root")\n',
                "",
            )
            module = types.ModuleType("trial_plugin_test_module")
            exec(compile(source, str(PLUGIN), "exec"), module.__dict__)
            return module


class FakeEvent:
    def __init__(self, extras):
        self.extras = extras
        self.stopped = False
        self.sent = []
        self.call_llm = False
    def get_extra(self, key, default=None):
        return self.extras.get(key, default)
    def stop_event(self):
        self.stopped = True
    async def send(self, value):
        self.sent.append(value)
    def plain_result(self, value):
        self.sent.append(value)
        return value


class FakeStore:
    def __init__(self):
        self.root = pathlib.Path(tempfile.gettempdir())
        self.prepare_calls = []
        self.lookups = []
    def mode(self):
        return "auto"
    def knowledge_data(self, question, group=None):
        self.lookups.append(("knowledge", group))
        return ["common fact", "group fact"]
    def game_data(self, question):
        self.lookups.append(("game", None))
        return ["game record"]
    def wiki_data(self, question):
        self.lookups.append(("wiki", None))
        return ["wiki record"]
    def prepare(self, *args, **kwargs):
        self.prepare_calls.append((args, kwargs))
        return ([{"role": "user", "content": "private history"}], "{}", "auto")


class TrialPluginDelegationTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_plugin()
        self.plugin = object.__new__(self.module.NapCatTrial)
        self.store = FakeStore()
        self.module.STORE = self.store
        self.module.unified_private = types.SimpleNamespace(
            lookup=lambda root, question: {"action": "answer", "material": "", "reply": ""},
            RULES="",
        )
        self.temp = tempfile.TemporaryDirectory()
        self.persona = pathlib.Path(self.temp.name) / "PERSONA.md"
        self.persona.write_text("我是奶蛙。", encoding="utf-8")
        self.module.PERSONA_PATH = self.persona

    def tearDown(self):
        self.temp.cleanup()

    def _req(self):
        return types.SimpleNamespace(
            func_tool=["unsafe"], extra_user_content_parts=["identifier"],
            contexts=[{"role": "user", "content": "chat_plus group context"}],
            prompt="plugin question", image_urls=["base64://synthetic"],
            system_prompt="chat_plus system", conversation="native",
        )

    def test_delegated_group_preserves_plugin_context_and_adds_only_safe_refs(self):
        state = {"kind": "group", "group_delegated": True, "question": "synthetic query",
                 "group": "synthetic-group", "speaker": "Display Name", "member_tag": "a" * 12,
                 "audit": lambda *a, **k: None}
        event = FakeEvent({"_napcat_trial_state": state, "_group_chat_plus_request": True})
        req = self._req()
        asyncio.run(self.plugin.capture_chatplus_claim(event, req))
        asyncio.run(self.plugin.prepare_request(event, req))
        self.assertFalse(event.stopped)
        self.assertEqual(req.prompt, "plugin question")
        self.assertEqual(req.image_urls, ["base64://synthetic"])
        self.assertEqual(req.contexts[0]["content"], "chat_plus group context")
        self.assertEqual(req.func_tool, None)
        self.assertEqual(req.extra_user_content_parts, [])
        self.assertIn("currentSpeaker", req.contexts[1]["content"])
        self.assertNotIn("nkwiki.cc", req.contexts[1]["content"])
        self.assertIn("【攻略资料】", req.system_prompt)
        self.assertIn("wiki record", req.system_prompt)
        self.assertIn("奶块知识库", req.system_prompt)
        self.assertIn("玩家整理、非官方", req.system_prompt)
        self.assertIn("我是奶蛙", req.system_prompt)
        self.assertIn("chat_plus system", req.system_prompt)
        self.assertLess(req.system_prompt.index("chat_plus system"), req.system_prompt.index("【攻略资料】"))
        self.assertEqual(self.store.prepare_calls, [])
        self.assertEqual(len(self.store.lookups), 3)

    def test_group_without_chatplus_marker_fails_closed_before_knowledge_or_model(self):
        state = {"kind": "group", "group_delegated": True, "question": "synthetic query",
                 "group": "synthetic-group", "audit": lambda *a, **k: None}
        event = FakeEvent({"_napcat_trial_state": state})
        req = self._req()
        asyncio.run(self.plugin.prepare_request(event, req))
        self.assertTrue(event.stopped)
        self.assertEqual(self.store.lookups, [])
        self.assertEqual(self.store.prepare_calls, [])

    def test_real_chatplus_marker_lifecycle_is_snapshotted_before_plugin_clears_it(self):
        # ReplyHandler.generate_reply sets this exact plugin marker before the
        # plugin's own priority -1 on_llm_request hook; that hook clears it.
        marker = "_group_chat_plus_request"
        state = {"kind": "group", "group_delegated": True, "question": "synthetic query",
                 "group": "synthetic-group", "audit": lambda *a, **k: None}
        event = FakeEvent({"_napcat_trial_state": state})
        req = self._req()

        async def reply_handler_marker_write():
            event.extras[marker] = True
        asyncio.run(reply_handler_marker_write())
        asyncio.run(self.plugin.capture_chatplus_claim(event, req))
        # Mirror the installed chat_plus on_llm_request hook's cleanup.
        event.extras[marker] = None
        asyncio.run(self.plugin.prepare_request(event, req))

        self.assertEqual(self.plugin.capture_chatplus_claim._test_priority, 0)
        self.assertEqual(self.plugin.prepare_request._test_priority, -2)
        self.assertTrue(state.get("chatplus_claimed"))
        self.assertIsNone(event.get_extra(marker))
        self.assertFalse(event.stopped)
        self.assertEqual(self.store.prepare_calls, [])
        self.assertEqual(len(self.store.lookups), 3)

    def test_delegated_group_memory_commands_explain_private_only_behavior(self):
        for command in ("/新对话", "/记住 synthetic note", "/记忆 2", "/忘记 1"):
            state = {"group_delegated": True, "question": command}
            event = FakeEvent({"_napcat_trial_state": state})
            async def run():
                async for _ in self.plugin.local_commands(event):
                    pass
            asyncio.run(run())
            self.assertTrue(event.stopped)
            self.assertEqual(len(event.sent), 1)
            self.assertIn("仅在私聊", event.sent[0])

    def test_group_chatplus_silence_closes_native_process_fallback_only(self):
        suppressed = FakeEvent({"_napcat_trial_state": {"group_delegated": True}})
        asyncio.run(self.plugin.suppress_unclaimed_group_fallback(suppressed))
        self.assertTrue(suppressed.call_llm)
        claimed = FakeEvent({"_napcat_trial_state": {"group_delegated": True, "chatplus_claimed": True}})
        asyncio.run(self.plugin.suppress_unclaimed_group_fallback(claimed))
        self.assertFalse(claimed.call_llm)
        self.assertFalse(claimed.stopped)
        legacy = FakeEvent({"_napcat_trial_state": {"group_delegated": False}})
        asyncio.run(self.plugin.suppress_unclaimed_group_fallback(legacy))
        self.assertFalse(legacy.call_llm)

    def test_private_path_keeps_existing_store_and_image_flow(self):
        state = {"kind": "private", "group_delegated": False, "question": "synthetic query",
                 "key": "private-key", "group": None, "images": ["base64://private"],
                 "audit": lambda *a, **k: None}
        event = FakeEvent({"_napcat_trial_state": state})
        req = self._req()
        asyncio.run(self.plugin.prepare_request(event, req))
        self.assertFalse(event.stopped)
        self.assertEqual(req.prompt, "synthetic query")
        self.assertEqual(req.image_urls, ["base64://private"])
        self.assertEqual(len(self.store.prepare_calls), 1)
        self.assertEqual(self.store.lookups, [])

    def test_legacy_group_path_remains_available_without_delegation_flag(self):
        state = {"kind": "group", "group_delegated": False, "question": "synthetic query",
                 "key": "group-key", "group": "synthetic-group", "images": [],
                 "audit": lambda *a, **k: None}
        event = FakeEvent({"_napcat_trial_state": state})
        req = self._req()
        asyncio.run(self.plugin.prepare_request(event, req))
        self.assertFalse(event.stopped)
        self.assertEqual(len(self.store.prepare_calls), 1)
        self.assertEqual(self.store.lookups, [])

    def test_history_retention_is_only_applied_to_allowlisted_group_rows(self):
        class Manager:
            def __init__(self):
                self.calls = []
            async def insert(self, **kwargs):
                self.calls.append(kwargs)
                return True

        manager = Manager()
        self.module.install_group_history_retention(manager, {"synthetic-group"}, limit=300)
        async def run():
            await manager.insert(platform_id="trial", user_id="synthetic-group", content={
                "segments": [
                    {"type": "text", "data": {"text": "safe text"}},
                    {"type": "image", "data": {"url": "https://private.invalid/image", "base64": "synthetic"}},
                ]
            })
            await manager.insert(platform_id="trial", user_id="synthetic-private", content={})
        asyncio.run(run())
        self.assertEqual(manager.calls[0]["max_messages"], 300)
        content = manager.calls[0]["content"]
        self.assertEqual(content["segments"][0]["data"]["text"], "safe text")
        self.assertEqual(content["segments"][1], {"type": "text", "data": {"text": "[图片或附件]"}})
        self.assertNotIn("max_messages", manager.calls[1])

    def test_history_retention_memory_off_skips_group_but_keeps_private(self):
        class Manager:
            def __init__(self): self.calls = []
            async def insert(self, **kwargs): self.calls.append(kwargs); return True
        manager = Manager()
        self.module.install_group_history_retention(manager, {"synthetic-group"})
        self.store.mode = lambda: "off"
        async def run():
            result = await manager.insert(platform_id="trial", user_id="synthetic-group", content={})
            await manager.insert(platform_id="trial", user_id="synthetic-private", content={})
            return result
        self.assertIsNone(asyncio.run(run()))
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(manager.calls[0]["user_id"], "synthetic-private")

    def test_chatplus_context_memory_modes_gate_only_group_history(self):
        module_name = "data.plugins.astrbot_plugin_group_chat_plus.utils.context_manager"
        module = types.ModuleType(module_name)
        class ContextManager:
            @staticmethod
            def get_history_messages(event, max_messages): return ["old"]
            @staticmethod
            async def get_history_messages_with_fallback(event, max_messages, context=None, cached_messages=None): return ["old"]
            @staticmethod
            def get_history_messages_by_params(platform_name, is_private, chat_id, max_messages): return ["old"]
            @staticmethod
            async def get_history_messages_by_params_with_fallback(platform_name, platform_id, is_private, chat_id, bot_id, max_messages, context=None, cached_messages=None): return ["old"]
            @staticmethod
            async def save_user_message(event, message_text, context=None, skip_custom_storage=False): return True
            @staticmethod
            async def save_bot_message(event, bot_message_text, context=None, skip_custom_storage=False): return True
            @staticmethod
            async def save_bot_message_by_params(platform_name, is_private, chat_id, bot_message_text, self_id=None, context=None, platform_id=None): return True
        module.ContextManager = ContextManager
        package = types.ModuleType("data")
        plugins = types.ModuleType("data.plugins")
        plugin = types.ModuleType("data.plugins.astrbot_plugin_group_chat_plus")
        utils = types.ModuleType("data.plugins.astrbot_plugin_group_chat_plus.utils")
        package.plugins = plugins
        plugins.astrbot_plugin_group_chat_plus = plugin
        plugin.utils = utils
        utils.context_manager = module
        with patch.dict(sys.modules, {"data": package,
                                      "data.plugins": plugins,
                                      "data.plugins.astrbot_plugin_group_chat_plus": plugin,
                                      "data.plugins.astrbot_plugin_group_chat_plus.utils": utils,
                                      module_name: module}):
            self.module.install_chatplus_memory_policy({"synthetic-group"})
            manager = ContextManager()
            group = types.SimpleNamespace(is_private_chat=lambda: False, get_group_id=lambda: "synthetic-group")
            private = types.SimpleNamespace(is_private_chat=lambda: True, get_group_id=lambda: None)
            self.store.mode = lambda: "manual"
            self.assertEqual(ContextManager.get_history_messages(group, 10), [])
            self.assertEqual(ContextManager.get_history_messages_by_params("p", False, "synthetic-group", 10), [])
            self.assertEqual(asyncio.run(ContextManager.get_history_messages_with_fallback(group, 10)), [])
            self.assertEqual(asyncio.run(ContextManager.get_history_messages_by_params_with_fallback(
                "p", "pid", False, "synthetic-group", "bot", 10)), [])
            self.assertFalse(asyncio.run(ContextManager.save_user_message(group, "new group row")))
            self.assertFalse(asyncio.run(ContextManager.save_bot_message_by_params("p", False, "synthetic-group", "reply")))
            self.assertEqual(ContextManager.get_history_messages(private, 10), ["old"])
            self.assertTrue(asyncio.run(ContextManager.save_user_message(private, "private row")))
            self.store.mode = lambda: "auto"
            self.assertEqual(ContextManager.get_history_messages(group, 10), ["old"])


if __name__ == "__main__":
    unittest.main()
