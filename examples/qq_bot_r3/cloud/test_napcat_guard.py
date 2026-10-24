import unittest
import sys
import types
from unittest.mock import patch

from cloud.napcat_guard import UNSUPPORTED_REPLY, inspect_event, install_guard


POLICY = {"self_id": "900000001", "owner_id": "900000002", "test_group_id": "900000003"}


def event(*, kind="private", sender="900000002", group="900000003", segments=None, **extra):
    result = {
        "post_type": "message",
        "message_type": kind,
        "self_id": "900000001",
        "user_id": sender,
        "group_id": group,
        "message": segments if segments is not None else [{"type": "text", "data": {"text": "synthetic prompt"}}],
    }
    result.update(extra)
    return result


class NapCatGuardTests(unittest.TestCase):
    def test_chatplus_group_delegation_keeps_legacy_default_and_safe_at(self):
        from cloud.napcat_guard import _safe_event_payload

        delegated = {**POLICY, "group_chat_owner": "chat_plus"}
        ordinary = event(kind="group", sender="900000004", message_id=1)
        result = inspect_event(ordinary, policy=delegated)
        self.assertEqual((result.decision, result.reason), ("accept", "group_delegated"))
        self.assertEqual(inspect_event(ordinary, policy=POLICY).decision, "observe")

        mentioned = event(kind="group", sender="900000004", message_id=2, segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "at", "data": {"qq": "all"}},
            {"type": "at", "data": {"qq": "900000009"}},
            {"type": "text", "data": {"text": "hello"}},
        ])
        result = inspect_event(mentioned, policy=delegated)
        payload = _safe_event_payload(mentioned, result)
        self.assertTrue(result.mentioned)
        self.assertEqual(payload["message"][0], {"type": "at", "data": {"qq": "900000001"}})
        self.assertEqual(len([part for part in payload["message"] if part["type"] == "at"]), 1)
        self.assertTrue(payload["sender"]["nickname"].startswith("成员"))
        self.assertNotIn("900000004", payload["sender"]["nickname"])

    def test_chatplus_unaddressed_images_are_marker_only_and_bad_segments_denied(self):
        delegated = {**POLICY, "group_chat_owner": "chat_plus"}
        image = event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "看看这个"}},
            {"type": "image", "data": {"url": "https://example.invalid/private"}},
        ])
        result = inspect_event(image, policy=delegated)
        self.assertEqual((result.decision, result.text, result.images), ("accept", "看看这个 [图片]", ()))
        unsupported = event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "普通聊天"}},
            {"type": "file", "data": {"url": "https://example.invalid/private"}},
        ])
        self.assertEqual(inspect_event(unsupported, policy=delegated).decision, "ignore")
        self.assertEqual(inspect_event(event(kind="group", group="900000005"), policy=delegated).decision, "deny")

    def test_owner_private_text_is_accepted(self):
        result = inspect_event(event(), policy=POLICY)
        self.assertEqual((result.decision, result.kind, result.text), ("accept", "private", "synthetic prompt"))

    def test_other_private_and_group_are_denied(self):
        self.assertEqual(inspect_event(event(sender="900000004"), policy=POLICY).decision, "deny")
        self.assertEqual(inspect_event(event(kind="group", sender="900000004", group="900000005"), policy=POLICY).decision, "deny")

    def test_group_requires_at_of_trial_bot(self):
        no_at = event(kind="group", sender="900000004")
        result = inspect_event(no_at, policy=POLICY)
        self.assertEqual((result.decision, result.kind, result.text, result.reason),
                         ("observe", "group", "synthetic prompt", "group_without_bot_mention"))
        with_at = event(kind="group", sender="900000004", segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "text", "data": {"text": "synthetic prompt"}},
        ])
        result = inspect_event(with_at, policy=POLICY)
        self.assertEqual((result.decision, result.kind, result.text), ("accept", "group", "synthetic prompt"))

    def test_overheard_group_text_never_reaches_native_conversion(self):
        class Adapter:
            async def convert_message(self, value):
                raise AssertionError("overheard group text must not reach the native converter")

        module = type("Module", (), {"AiocqhttpAdapter": Adapter, "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        rows, overheard = [], []

        def record(group, entry):
            overheard.append((group, entry))
            return True

        install_guard(module, policy=POLICY, audit=lambda *args, **kwargs: rows.append((args, kwargs)), record=record)
        import asyncio
        import time as clock
        raw = event(kind="group", sender="900000004", message_id=77, time=int(clock.time()),
                    segments=[{"type": "text", "data": {"text": "group chatter"}}])
        raw["sender"] = {"user_id": "900000004", "card": "空调房里晒太阳", "nickname": "nick-token-escape"}
        self.assertIsNone(asyncio.run(module.AiocqhttpAdapter.convert_message(object(), raw)))
        self.assertEqual(len(overheard), 1)
        group, entry = overheard[0]
        self.assertEqual(group, "900000003")
        self.assertEqual(entry["name"], "空调房里晒太阳")
        self.assertEqual(entry["text"], "group chatter")
        self.assertNotIn("900000004", str(entry))
        self.assertEqual(len(entry["member"]), 12)
        self.assertEqual(rows, [(("observed",), {"kind": "group", "reason": "recorded"})])

    def test_overheard_when_recorder_declines_is_audited_as_off(self):
        class Adapter:
            async def convert_message(self, value):
                raise AssertionError("overheard group text must not reach the native converter")

        module = type("Module", (), {"AiocqhttpAdapter": Adapter, "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        rows = []
        install_guard(module, policy=POLICY, audit=lambda *args, **kwargs: rows.append((args, kwargs)),
                      record=lambda group, entry: False)
        import asyncio
        asyncio.run(module.AiocqhttpAdapter.convert_message(object(), event(kind="group", sender="900000004", message_id=78)))
        self.assertEqual(rows, [(("observed",), {"kind": "group", "reason": "listen_off"})])

    def test_empty_group_text_is_ignored_not_recorded(self):
        self.assertEqual(inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "   "}},
        ]), policy=POLICY).reason, "empty_text")
        self.assertEqual(inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "image", "data": {"url": "synthetic-private-url"}},
        ]), policy=POLICY).decision, "ignore")

    def test_self_messages_and_non_message_events_are_ignored(self):
        self.assertEqual(inspect_event(event(sender="900000001"), policy=POLICY).decision, "ignore")
        self.assertEqual(inspect_event(event(post_type="notice"), policy=POLICY).decision, "ignore")

    def test_file_and_voice_segments_still_return_fixed_notice(self):
        for kind in ("file", "record", "video"):
            result = inspect_event(event(segments=[{"type": kind, "data": {"url": "synthetic"}}]), policy=POLICY)
            self.assertEqual((result.decision, result.reason), ("fixed_reply", "unsupported_segment"))
            self.assertEqual(result.text, "")
            self.assertEqual(result.images, ())
        self.assertIn("暂不支持", UNSUPPORTED_REPLY)

    def test_quote_is_ignored_and_the_text_is_answered(self):
        result = inspect_event(event(segments=[
            {"type": "reply", "data": {"id": "12345"}},
            {"type": "text", "data": {"text": "这个装备怎么合成"}},
        ]), policy=POLICY)
        self.assertEqual((result.decision, result.text), ("accept", "这个装备怎么合成"))

    def test_quote_only_message_is_ignored(self):
        result = inspect_event(event(segments=[{"type": "reply", "data": {"id": "12345"}}]), policy=POLICY)
        self.assertEqual(result.decision, "ignore")

    def test_other_at_and_at_all_do_not_block_the_answer(self):
        for target in ("900000009", "all"):
            result = inspect_event(event(kind="group", segments=[
                {"type": "at", "data": {"qq": target}},
                {"type": "at", "data": {"qq": "900000001"}},
                {"type": "text", "data": {"text": "帮我看看"}},
            ]), policy=POLICY)
            self.assertEqual((result.decision, result.text), ("accept", "帮我看看"))

    def test_at_all_without_mention_is_listened_not_answered(self):
        result = inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "at", "data": {"qq": "all"}},
            {"type": "text", "data": {"text": "今晚开会"}},
        ]), policy=POLICY)
        self.assertEqual((result.decision, result.text), ("observe", "今晚开会"))

    def test_quote_with_keyword_still_triggers_an_answer(self):
        result = inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "reply", "data": {"id": "12345"}},
            {"type": "text", "data": {"text": "奶蛙你看这个"}},
        ]), policy={**POLICY, "group_keywords": ["奶蛙"]})
        self.assertEqual(result.reason, "keyword_triggered")

    def test_owner_private_image_is_accepted_and_forwarded_as_reference(self):
        result = inspect_event(event(segments=[
            {"type": "text", "data": {"text": "这是什么"}},
            {"type": "image", "data": {"file": "abc.image", "url": "https://multimedia.nt.qq.com.cn/synthetic"}},
        ]), policy=POLICY)
        self.assertEqual((result.decision, result.reason), ("accept", "authorized_text"))
        self.assertEqual(result.text, "这是什么")
        self.assertEqual(result.images, ("https://multimedia.nt.qq.com.cn/synthetic",))

    def test_image_without_usable_reference_falls_back_to_fixed_notice(self):
        for data in ({"file": "abc.image"}, {"url": "file:///etc/passwd"}, {"url": "https://x/" + "a" * 2_000_001}):
            result = inspect_event(event(segments=[{"type": "image", "data": data}]), policy=POLICY)
            self.assertEqual(result.decision, "fixed_reply")

    def test_image_only_message_is_accepted_with_empty_text(self):
        result = inspect_event(event(segments=[
            {"type": "image", "data": {"url": "https://multimedia.nt.qq.com.cn/synthetic"}},
        ]), policy=POLICY)
        self.assertEqual((result.decision, result.text, len(result.images)), ("accept", "", 1))

    def test_extra_images_beyond_cap_are_dropped_not_forwarded(self):
        from cloud.napcat_guard import MAX_IMAGES_PER_MESSAGE
        segments = [{"type": "image", "data": {"url": f"https://example.invalid/{i}"}}
                    for i in range(MAX_IMAGES_PER_MESSAGE + 2)]
        result = inspect_event(event(segments=segments), policy=POLICY)
        self.assertEqual(result.decision, "accept")
        self.assertEqual(len(result.images), MAX_IMAGES_PER_MESSAGE)

    def test_group_image_without_mention_enters_listening_with_marker(self):
        result = inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "image", "data": {"url": "https://example.invalid/a"}},
        ]), policy=POLICY)
        self.assertEqual((result.decision, result.reason), ("observe", "group_without_bot_mention"))
        self.assertEqual(result.text, "[图片]")
        self.assertEqual(result.images, ())

    def test_group_image_with_text_keeps_both_in_the_listening_line(self):
        result = inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "你看这个"}},
            {"type": "image", "data": {"url": "https://example.invalid/a"}},
            {"type": "image", "data": {"url": "https://example.invalid/b"}},
        ]), policy=POLICY)
        self.assertEqual(result.text, "你看这个 [图片×2]")
        self.assertEqual(result.images, ())

    def test_group_keyword_triggers_answer_without_at(self):
        policy = {**POLICY, "group_keywords": ["奶蛙", "在吗"]}
        hit = event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "奶蛙今天怎么不说话"}},
        ])
        miss = event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "今天天气不错"}},
        ])
        result = inspect_event(hit, policy=policy)
        self.assertEqual((result.decision, result.reason), ("accept", "keyword_triggered"))
        self.assertEqual(inspect_event(miss, policy=policy).decision, "observe")

    def test_keyword_list_is_ignored_when_absent_or_malformed(self):
        text_only = event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "奶蛙在吗"}},
        ])
        self.assertEqual(inspect_event(text_only, policy=POLICY).decision, "observe")
        for bad in ({"group_keywords": "奶蛙"}, {"group_keywords": [""]},
                    {"group_keywords": ["x" * 17]}, {"group_keywords": [f"k{i}" for i in range(33)]}):
            self.assertEqual(inspect_event(text_only, policy={**POLICY, **bad}).decision, "observe")

    def test_keyword_match_is_case_folded(self):
        policy = {**POLICY, "group_keywords": ["naIwa"]}
        result = inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "hey NAIWA"}},
        ]), policy=policy)
        self.assertEqual(result.reason, "keyword_triggered")

    def test_fail_closed_on_wrong_self_id_or_incomplete_policy(self):
        self.assertEqual(inspect_event(event(self_id="900000009"), policy=POLICY).reason, "self_id_mismatch")
        self.assertEqual(inspect_event(event(), policy={}).decision, "deny")

    def test_signed_onebot_message_ids_are_preserved_as_integers(self):
        from cloud.napcat_guard import _integer, _safe_event_payload
        raw = event(message_id="-42")
        result = inspect_event(raw, policy=POLICY)
        self.assertEqual(_safe_event_payload(raw, result)["message_id"], -42)
        self.assertEqual(_integer(0), 0)
        self.assertIsNone(_integer(True))
        self.assertIsNone(_integer("4-2"))

    def test_malformed_group_without_provable_mention_is_denied(self):
        malformed = event(kind="group", sender="900000004", segments="not-a-segment-list")
        no_sender = event(kind="group", segments=[{"type": "text", "data": {"text": "synthetic"}}])
        no_sender["user_id"] = None
        bad_sender = event(kind="group")
        bad_sender["user_id"] = "９０００００００４"
        for candidate in (malformed, no_sender, bad_sender):
            self.assertEqual(inspect_event(candidate, policy=POLICY).decision, "deny")

    def test_unmentioned_group_media_is_archived_and_untrusted_private_media_denied(self):
        group_image = event(kind="group", sender="900000004", segments=[
            {"type": "image", "data": {"url": "https://example.invalid/synthetic"}},
        ])
        other_private_image = event(sender="900000004", segments=[
            {"type": "image", "data": {"url": "https://example.invalid/synthetic"}},
        ])
        self.assertEqual(inspect_event(group_image, policy=POLICY).decision, "observe")
        self.assertEqual(inspect_event(other_private_image, policy=POLICY).decision, "deny")

    def test_installed_guard_passes_only_sanitized_text_to_native_converter(self):
        class FakeEvent(dict):
            @classmethod
            def from_payload(cls, payload):
                return cls(payload)

        class FakeBot:
            async def call_action(self, *args, **kwargs):
                raise AssertionError("authorized text should not issue actions")

        class Adapter:
            async def convert_message(self, value):
                self.seen = value
                return type("Converted", (), {"raw_message": value})()

        module = type("Module", (), {"AiocqhttpAdapter": Adapter, "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        audit_rows = []
        install_guard(module, policy=POLICY, audit=lambda *args, **kwargs: audit_rows.append((args, kwargs)))
        import asyncio
        adapter = Adapter()
        raw_data = event(kind="group", sender="900000004", segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "text", "data": {"text": "synthetic prompt"}},
        ], message_id=42, secret_extra="must_not_escape")
        raw_data["sender"] = {"user_id": "900000004", "nickname": "nick-token-escape"}
        raw = FakeEvent(raw_data)
        converted = asyncio.run(adapter.convert_message(raw))
        self.assertEqual(converted.raw_message["message"], [{"type": "text", "data": {"text": "synthetic prompt"}}])
        self.assertNotIn("secret_extra", converted.raw_message)
        self.assertEqual(converted.raw_message["sender"]["nickname"], "")
        self.assertEqual(converted.raw_message["sender"]["card"], "")
        state = converted._napcat_trial_state
        self.assertEqual(state["speaker"], "nick-token-escape")
        self.assertEqual(len(state["member_tag"]), 12)
        self.assertIsNone(state["event_time_ms"])
        self.assertNotIn("nick-token-escape", str(converted.raw_message))
        self.assertEqual(audit_rows, [(('accepted',), {"kind": "group", "reason": "authorized_text"})])

    def test_delegated_verified_at_bypasses_converter_lookup_then_restores_native_at(self):
        import asyncio

        class FakeEvent(dict):
            @classmethod
            def from_payload(cls, payload):
                return cls(payload)

        class FakeAt:
            def __init__(self, *, qq, name=""):
                self.qq = qq
                self.name = name

        class FakeBot:
            async def call_action(self, action, **kwargs):
                raise AssertionError("native conversion must not perform member API lookups")

        class Converted:
            def __init__(self):
                self.chain = []
                self.raw_message = None

            def get_messages(self):
                return self.chain

        class Adapter:
            def __init__(self):
                self.bot = FakeBot()
                self.inputs = []

            async def convert_message(self, value):
                self.inputs.append(value)
                for segment in value["message"]:
                    if segment.get("type") == "at":
                        await self.bot.call_action("get_group_member_info")
                return Converted()

        components = types.ModuleType("astrbot.api.message_components")
        components.At = FakeAt
        api = types.ModuleType("astrbot.api")
        api.__path__ = []
        api.message_components = components
        astrbot = types.ModuleType("astrbot")
        astrbot.__path__ = []
        module = type("Module", (), {
            "AiocqhttpAdapter": Adapter,
            "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)}),
        })
        policy = {**POLICY, "group_chat_owner": "chat_plus"}
        install_guard(module, policy=policy, audit=lambda *a, **k: None)
        adapter = Adapter()
        raw = FakeEvent(event(kind="group", sender="900000004", message_id=9801, segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "text", "data": {"text": "hello"}},
        ]))
        # The mention-only case still carries an empty Plain segment through
        # the converter so the OneBot event remains structurally convertible.
        pure_at = FakeEvent(event(kind="group", sender="900000004", message_id=9802, segments=[
            {"type": "at", "data": {"qq": "900000001"}},
        ]))
        with patch.dict(sys.modules, {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.message_components": components,
        }):
            converted = asyncio.run(adapter.convert_message(raw))
            converted_pure_at = asyncio.run(adapter.convert_message(pure_at))

        self.assertTrue(all(not any(seg.get("type") == "at" for seg in item["message"])
                            for item in adapter.inputs))
        self.assertEqual(adapter.inputs[0]["message"], [{"type": "text", "data": {"text": "hello"}}])
        self.assertEqual(adapter.inputs[1]["message"], [{"type": "text", "data": {"text": ""}}])
        self.assertIsInstance(converted.get_messages()[0], FakeAt)
        self.assertEqual((converted.get_messages()[0].qq, converted.get_messages()[0].name), ("900000001", ""))
        self.assertFalse(hasattr(converted, "message"))
        self.assertEqual(converted.raw_message["message"][0], {"type": "at", "data": {"qq": "900000001"}})
        self.assertTrue(converted._napcat_trial_state["mentioned"])
        self.assertTrue(converted._napcat_trial_state["group_delegated"])
        self.assertIsInstance(converted_pure_at.get_messages()[0], FakeAt)

    def test_converted_image_payload_is_a_minimal_reference(self):
        class FakeEvent(dict):
            @classmethod
            def from_payload(cls, payload):
                return cls(payload)

        class Adapter:
            async def convert_message(self, value):
                self.seen = value
                return type("Converted", (), {"raw_message": value})()

        module = type("Module", (), {"AiocqhttpAdapter": Adapter, "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        install_guard(module, policy=POLICY, audit=lambda *a, **k: None)
        import asyncio
        raw = FakeEvent(event(segments=[
            {"type": "text", "data": {"text": "look"}},
            {"type": "image", "data": {"file": "x.image", "url": "https://example.invalid/p.png", "extra": "drop-me"}},
        ], message_id=51))
        converted = asyncio.run(Adapter().convert_message(raw))
        self.assertEqual(converted.raw_message["message"][1],
                         {"type": "image", "data": {"file": "https://example.invalid/p.png",
                                                    "url": "https://example.invalid/p.png"}})
        self.assertEqual(converted._napcat_trial_state["images"], ["https://example.invalid/p.png"])

    def test_keyword_trigger_sets_wake_flags_on_the_real_event(self):
        class FakeEvent(dict):
            @classmethod
            def from_payload(cls, payload):
                return cls(payload)

        class Adapter:
            async def convert_message(self, value):
                return type("Converted", (), {"raw_message": value, "is_at_or_wake_command": False,
                                              "is_wake": False})()

            @staticmethod
            def create_event(self, message):
                return type("Event", (), {"is_at_or_wake_command": False, "is_wake": False})()

        module = type("Module", (), {"AiocqhttpAdapter": Adapter, "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        policy = {**POLICY, "group_keywords": ["奶蛙"]}
        install_guard(module, policy=policy, audit=lambda *a, **k: None)
        import asyncio
        adapter = Adapter()
        raw = FakeEvent(event(kind="group", sender="900000004", segments=[
            {"type": "text", "data": {"text": "奶蛙在干嘛"}},
        ], message_id=52))
        converted = asyncio.run(adapter.convert_message(raw))
        self.assertTrue(getattr(converted, "_napcat_trial_force_wake", False))
        built = Adapter.create_event(adapter, converted)
        self.assertTrue(built.is_at_or_wake_command and built.is_wake)

    def test_keyword_match_does_not_wake_when_message_already_mentioned(self):
        class FakeEvent(dict):
            @classmethod
            def from_payload(cls, payload):
                return cls(payload)

        class Adapter:
            async def convert_message(self, value):
                return type("Converted", (), {"raw_message": value})()

        module = type("Module", (), {"AiocqhttpAdapter": Adapter, "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        install_guard(module, policy={**POLICY, "group_keywords": ["奶蛙"]}, audit=lambda *a, **k: None)
        import asyncio
        raw = FakeEvent(event(kind="group", sender="900000004", segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "text", "data": {"text": "奶蛙在干嘛"}},
        ], message_id=53))
        converted = asyncio.run(Adapter().convert_message(raw))
        self.assertFalse(getattr(converted, "_napcat_trial_force_wake", False))

    def test_mention_without_content_waits_instead_of_dropping(self):
        result = inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "at", "data": {"qq": "900000001"}},
        ]), policy=POLICY)
        self.assertEqual((result.decision, result.reason), ("mention_wait", "mention_without_content"))

    def test_mention_with_only_whitespace_also_waits(self):
        result = inspect_event(event(kind="group", sender="900000004", segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "text", "data": {"text": "  \n "}},
        ]), policy=POLICY)
        self.assertEqual(result.decision, "mention_wait")

    def test_private_empty_text_is_still_ignored(self):
        result = inspect_event(event(kind="private", segments=[
            {"type": "text", "data": {"text": "   "}},
        ]), policy=POLICY)
        self.assertEqual(result.decision, "ignore")

    def test_mention_only_sends_one_ack_after_the_wait(self):
        import asyncio

        class Bot:
            def __init__(self):
                self.calls = []

            async def call_action(self, action, **params):
                self.calls.append((action, params))
                return {"message_id": 1234}

        rows = []
        module = type("Module", (), {
            "AiocqhttpAdapter": type("Adapter", (), {
                "convert_message": staticmethod(lambda self, value: None),
            }),
            "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})

        guard = __import__("cloud.napcat_guard", fromlist=["x"])
        guard.MENTION_WAIT_SECONDS = 0.05
        guard._PENDING_ACK.clear()
        install_guard(module, policy=POLICY, audit=lambda *a, **k: rows.append((a, k)))

        async def run():
            adapter = module.AiocqhttpAdapter()
            adapter.bot = Bot()
            raw = event(kind="group", sender="900000004", message_id=9001, segments=[
                {"type": "at", "data": {"qq": "900000001"}},
            ])
            await adapter.convert_message(raw)
            await asyncio.sleep(0.2)
            return adapter.bot

        bot = asyncio.run(run())
        self.assertEqual(len(bot.calls), 1)
        action, params = bot.calls[0]
        self.assertEqual(action, "send_msg")
        self.assertEqual(params["group_id"], 900000003)
        self.assertEqual(params["message"], guard.MENTION_ONLY_REPLY)
        self.assertIn((("mention_without_content",), {"kind": "group", "reason": "waiting"}), rows)
        self.assertIn((("mention_ack_sdk_success",), {"kind": "group"}), rows)

    def test_followup_text_cancels_the_ack_and_is_answered(self):
        import asyncio

        class Bot:
            def __init__(self):
                self.calls = []

            async def call_action(self, action, **params):
                self.calls.append((action, params))
                return {"message_id": 1234}

        class Converted:
            raw_message = {}

        class Adapter:
            @staticmethod
            async def convert_message(self, value):
                converted = Converted()
                converted.raw_message = value
                return converted

        module = type("Module", (), {
            "AiocqhttpAdapter": Adapter,
            "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        guard = __import__("cloud.napcat_guard", fromlist=["x"])
        guard.MENTION_WAIT_SECONDS = 0.3
        guard._PENDING_ACK.clear()
        rows = []
        install_guard(module, policy=POLICY, audit=lambda *a, **k: rows.append((a, k)))

        async def run():
            adapter = Adapter()
            adapter.bot = Bot()
            await adapter.convert_message(event(kind="group", sender="900000004", message_id=9101, segments=[
                {"type": "at", "data": {"qq": "900000001"}},
            ]))
            converted = await adapter.convert_message(event(kind="group", sender="900000004", message_id=9102, segments=[
                {"type": "text", "data": {"text": "今天吃什么"}},
            ]))
            await asyncio.sleep(0.5)
            return adapter.bot, converted

        bot, converted = asyncio.run(run())
        self.assertEqual(bot.calls, [])
        self.assertEqual(converted._napcat_trial_state["question"], "今天吃什么")
        self.assertTrue(getattr(converted, "_napcat_trial_force_wake", False))
        self.assertIn((("accepted",), {"kind": "group", "reason": "mention_followup"}), rows)

    def test_followup_from_another_member_does_not_cancel_the_ack(self):
        import asyncio

        class Bot:
            def __init__(self):
                self.calls = []

            async def call_action(self, action, **params):
                self.calls.append((action, params))
                return {"message_id": 77}

        module = type("Module", (), {
            "AiocqhttpAdapter": type("Adapter", (), {
                "convert_message": staticmethod(lambda self, value: None),
            }),
            "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        guard = __import__("cloud.napcat_guard", fromlist=["x"])
        guard.MENTION_WAIT_SECONDS = 0.15
        guard._PENDING_ACK.clear()
        install_guard(module, policy=POLICY, audit=lambda *a, **k: None)

        async def run():
            adapter = module.AiocqhttpAdapter()
            adapter.bot = Bot()
            await adapter.convert_message(event(kind="group", sender="900000004", message_id=9201, segments=[
                {"type": "at", "data": {"qq": "900000001"}},
            ]))
            await adapter.convert_message(event(kind="group", sender="900000005", message_id=9202, segments=[
                {"type": "text", "data": {"text": "别人说话"}},
            ]))
            await asyncio.sleep(0.35)
            return adapter.bot

        bot = asyncio.run(run())
        self.assertEqual(len(bot.calls), 1)

    def test_text_then_mention_answers_the_earlier_text(self):
        import asyncio

        class Converted:
            raw_message = {}

        class Adapter:
            @staticmethod
            async def convert_message(self, value):
                converted = Converted()
                converted.raw_message = value
                return converted

        module = type("Module", (), {
            "AiocqhttpAdapter": Adapter,
            "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        guard = __import__("cloud.napcat_guard", fromlist=["x"])
        guard.MENTION_WAIT_SECONDS = 0.2
        guard._PENDING_ACK.clear()
        guard._RECENT_GROUP_TEXT.clear()
        rows = []
        install_guard(module, policy=POLICY, audit=lambda *a, **k: rows.append((a, k)))

        async def run():
            adapter = Adapter()
            adapter.bot = type("Bot", (), {"call_action": staticmethod(lambda *a, **k: {"message_id": 1})})()
            first = await adapter.convert_message(event(kind="group", sender="900000004", message_id=9501, segments=[
                {"type": "text", "data": {"text": "明天几点集合"}},
            ]))
            second = await adapter.convert_message(event(kind="group", sender="900000004", message_id=9502, segments=[
                {"type": "at", "data": {"qq": "900000001"}},
            ]))
            await asyncio.sleep(0.3)
            return first, second

        first, second = asyncio.run(run())
        self.assertIsNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(second._napcat_trial_state["question"], "明天几点集合")
        self.assertTrue(getattr(second, "_napcat_trial_force_wake", False))
        self.assertIn((("accepted",), {"kind": "group", "reason": "mention_after_text"}), rows)

    def test_stale_text_before_mention_falls_back_to_ack(self):
        import asyncio

        got = []

        class Bot:
            async def call_action(self, action, **params):
                got.append(params)
                return {"message_id": 9}

        module = type("Module", (), {
            "AiocqhttpAdapter": type("Adapter", (), {
                "convert_message": staticmethod(lambda self, value: None),
            }),
            "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        guard = __import__("cloud.napcat_guard", fromlist=["x"])
        guard.MENTION_WAIT_SECONDS = 0.12
        guard._PENDING_ACK.clear()
        guard._RECENT_GROUP_TEXT.clear()
        guard._RECENT_TEXT_TTL = 0.05
        install_guard(module, policy=POLICY, audit=lambda *a, **k: None)

        async def run():
            adapter = module.AiocqhttpAdapter()
            adapter.bot = Bot()
            await adapter.convert_message(event(kind="group", sender="900000004", message_id=9601, segments=[
                {"type": "text", "data": {"text": "旧话题"}},
            ]))
            await asyncio.sleep(0.15)
            await adapter.convert_message(event(kind="group", sender="900000004", message_id=9602, segments=[
                {"type": "at", "data": {"qq": "900000001"}},
            ]))
            await asyncio.sleep(0.35)

        asyncio.run(run())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["message"], guard.MENTION_ONLY_REPLY)

    def test_mention_then_mention_reuses_nothing(self):
        import asyncio

        module = type("Module", (), {
            "AiocqhttpAdapter": type("Adapter", (), {
                "convert_message": staticmethod(lambda self, value: None),
            }),
            "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        guard = __import__("cloud.napcat_guard", fromlist=["x"])
        guard.MENTION_WAIT_SECONDS = 5
        guard._PENDING_ACK.clear()
        guard._RECENT_GROUP_TEXT.clear()
        rows = []
        install_guard(module, policy=POLICY, audit=lambda *a, **k: rows.append((a, k)))

        async def run():
            adapter = module.AiocqhttpAdapter()
            adapter.bot = type("Bot", (), {"call_action": staticmethod(lambda *a, **k: {"message_id": 1})})()
            await adapter.convert_message(event(kind="group", sender="900000004", message_id=9701, segments=[
                {"type": "at", "data": {"qq": "900000001"}},
            ]))
            await adapter.convert_message(event(kind="group", sender="900000004", message_id=9702, segments=[
                {"type": "at", "data": {"qq": "900000001"}},
            ]))

        asyncio.run(run())
        for task in list(guard._PENDING_ACK.values()):
            task.cancel()
        guard._PENDING_ACK.clear()
        reasons = [kwargs.get("reason") for args, kwargs in rows if args and args[0] == "accepted"]
        self.assertNotIn("mention_after_text", reasons)

    def test_multiple_allowed_groups_are_accepted(self):
        policy = {**POLICY, "group_ids": ["900000003", "900000777"]}
        with_at = event(kind="group", sender="900000004", group="900000777", segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "text", "data": {"text": "hi"}},
        ])
        self.assertEqual(inspect_event(with_at, policy=policy).decision, "accept")
        stranger = event(kind="group", sender="900000004", group="900000888", segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "text", "data": {"text": "hi"}},
        ])
        self.assertEqual(inspect_event(stranger, policy=policy).decision, "deny")

    def test_group_ids_list_is_capped_and_ignores_garbage(self):
        policy = {**POLICY, "group_ids": ["900000003", "not-a-number", None]}
        with_at = event(kind="group", sender="900000004", segments=[
            {"type": "at", "data": {"qq": "900000001"}},
            {"type": "text", "data": {"text": "hi"}},
        ])
        self.assertEqual(inspect_event(with_at, policy=policy).decision, "accept")

    def test_member_tag_is_group_scoped_and_event_time_is_clamped(self):
        from cloud.napcat_guard import _event_time_ms, _member_tag
        import time as _time
        self.assertNotEqual(_member_tag("900000003", "900000004"), _member_tag(None, "900000004"))
        self.assertEqual(_member_tag("900000003", "900000004"), _member_tag("900000003", "900000004"))
        self.assertNotEqual(_member_tag("900000003", "900000004"), _member_tag("900000003", "900000005"))
        epoch = int(_time.time())
        self.assertEqual(_event_time_ms({"time": epoch - 60}), (epoch - 60) * 1000)
        for unusable in ({}, {"time": 0}, {"time": "x"}, {"time": True}, {"time": epoch - 8 * 86400}, {"time": epoch + 600}):
            self.assertIsNone(_event_time_ms(unusable))

    def test_display_name_prefers_card_and_stays_display_only(self):
        from cloud.napcat_guard import _display_name
        self.assertEqual(_display_name({"sender": {"card": "  群主   本人  ", "nickname": "synthetic"}}), "群主 本人")
        self.assertEqual(_display_name({"sender": {"nickname": "x" * 40}}), "x" * 24)
        self.assertEqual(_display_name({"sender": {"card": "\n", "nickname": "ok"}}), "ok")
        self.assertEqual(_display_name({"sender": "not-a-dict"}), "")
        self.assertEqual(_display_name({}), "")
        self.assertEqual(_display_name({"sender": {"nickname": 12}}), "")

    def test_fixed_notice_sends_only_on_allowlisted_unsupported_event(self):
        class FakeBot:
            def __init__(self):
                self.calls = []
            async def call_action(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                # aiocqhttp 1.4.4 returns the OneBot `data` object.
                return {"message_id": 1}

        class Adapter:
            def __init__(self):
                self.bot = FakeBot()
            async def convert_message(self, value):
                raise AssertionError("fixed notice must bypass native converter")

        module = type("Module", (), {"AiocqhttpAdapter": Adapter, "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        audit_rows = []
        install_guard(module, policy=POLICY, audit=lambda *args, **kwargs: audit_rows.append((args, kwargs)))
        import asyncio
        adapter = Adapter()
        unsupported = event(segments=[{"type": "image", "data": {"url": "synthetic-private-url"}}], message_id=43)
        self.assertIsNone(asyncio.run(adapter.convert_message(unsupported)))
        self.assertEqual(len(adapter.bot.calls), 1)
        self.assertEqual(adapter.bot.calls[0][0], ("send_msg",))
        self.assertEqual(adapter.bot.calls[0][1], {"message": UNSUPPORTED_REPLY, "message_type": "private", "user_id": 900000002})
        self.assertEqual(audit_rows, [(('fixed_notice_sdk_success',), {"kind": "private"})])

    def test_denied_events_never_reach_native_or_send_actions(self):
        class FakeBot:
            def __init__(self):
                self.calls = []
            async def call_action(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return {"retcode": 0, "message_id": 1}

        class Adapter:
            def __init__(self):
                self.bot = FakeBot()
                self.native_calls = 0
            async def convert_message(self, value):
                self.native_calls += 1
                return object()

        module = type("Module", (), {"AiocqhttpAdapter": Adapter, "logger": type("Logger", (), {"debug": staticmethod(lambda *a, **k: None)})})
        install_guard(module, policy=POLICY, audit=lambda *args, **kwargs: None)
        import asyncio
        adapter = Adapter()
        no_mention = event(kind="group", sender="900000004", segments=[{"type": "image", "data": {"url": "synthetic"}}])
        bad_sender = event(kind="group", segments=[{"type": "text", "data": {"text": "synthetic"}}])
        bad_sender["user_id"] = None
        unauthorized_private = event(sender="900000004", segments=[{"type": "image", "data": {"url": "synthetic"}}])
        for candidate in (no_mention, bad_sender, unauthorized_private):
            self.assertIsNone(asyncio.run(adapter.convert_message(candidate)))
        self.assertEqual(adapter.native_calls, 0)
        self.assertEqual(adapter.bot.calls, [])


if __name__ == "__main__":
    unittest.main()
