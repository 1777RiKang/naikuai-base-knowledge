"""Fail-closed, payload-minimizing gate for a personal OneBot V11 trial.

This module contains no account identifiers. The caller must load an exact
private policy and must call ``inspect_event`` before AstrBot's native
``AiocqhttpAdapter.convert_message``.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import os
import pathlib
import time
from dataclasses import dataclass
from typing import Any


TRIAL_ROOT = "/var/lib/astrbot-naiwa-trial"
UNSUPPORTED_REPLY = "当前试运行只处理文字、@ 与图片；引用、文件和语音暂不支持。"
MENTION_ONLY_REPLY = "在的，怎么啦？"
MENTION_WAIT_SECONDS = 8.0
FIXED_NOTICE_ACTIVE = contextvars.ContextVar("napcat_trial_fixed_notice", default=False)
_SEEN_EVENTS: dict[tuple[str, int], float] = {}
_MAX_SEEN_EVENTS = 10000
_PENDING_ACK: dict[tuple[str, str], float] = {}
_RECENT_GROUP_TEXT: dict[tuple[str, str], tuple[float, str]] = {}
_RECENT_TEXT_TTL = 60.0
_MAX_RECENT_TEXT = 200
MAX_IMAGES_PER_MESSAGE = 2
_IMAGE_PREFIXES = ("http://", "https://", "base64://")


@dataclass(frozen=True)
class GateResult:
    decision: str  # accept | observe | mention_wait | fixed_reply | deny | ignore
    kind: str | None = None  # private | group
    text: str = ""
    reason: str = ""
    images: tuple[str, ...] = ()
    mentioned: bool = False


def _nonempty(value: Any) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        value = str(value)
        return value if value and all("0" <= char <= "9" for char in value) else None
    return None


def _display_name(event: dict[str, Any]) -> str:
    """Group card title first, then nickname. Runtime display data only: it never
    enters the sanitized payload, the archive, or any authorization decision.
    """
    sender_node = event.get("sender")
    if not isinstance(sender_node, dict):
        return ""
    for field in ("card", "nickname"):
        value = sender_node.get(field)
        if isinstance(value, str):
            collapsed = " ".join(value.split())[:24]
            if collapsed:
                return collapsed
    return ""


def _event_time_ms(event: dict[str, Any]) -> int | None:
    """Wall-clock time OneBot reports for the message, kept only when plausible.

    Returns ``None`` when absent or out of range so the store falls back to the
    commit-time clock instead of guessing.
    """
    raw = _integer(event.get("time"))
    if raw is None:
        return None
    epoch_ms = int(time.time() * 1000)
    if not epoch_ms - 7 * 86400 * 1000 <= raw * 1000 <= epoch_ms + 300 * 1000:
        return None
    return raw * 1000


def _member_tag(group: str | None, sender: str) -> str:
    return hashlib.sha256(("member:" + (group or "c2c") + ":" + sender).encode()).hexdigest()[:12]


def _image_reference(data: dict[str, Any]) -> str | None:
    """Accept only image references the native converter can fetch itself.

    OneBot gives NapCat images as ``file`` plus an ``url``; AstrBot's adapter
    understands http(s) and base64 URIs. Anything else is treated as
    unsupported instead of being passed through.
    """
    for field in ("url", "file"):
        value = data.get(field)
        if isinstance(value, str) and value.startswith(_IMAGE_PREFIXES) and len(value) <= 2_000_000:
            return value
    return None


def _keyword_hit(text: str, policy: dict[str, Any]) -> bool:
    """A group message without an @ may still be answered on a keyword match.

    Keywords live in the private policy file, are compared case-folded, and are
    capped in count/length so a malformed policy fails closed instead of
    matching everything.
    """
    keywords = policy.get("group_keywords")
    if not isinstance(text, str) or not text or not isinstance(keywords, list):
        return False
    if len(keywords) > 32 or not all(isinstance(word, str) and 1 <= len(word) <= 16 for word in keywords):
        return False
    folded = text.casefold()
    return any(word.casefold() in folded for word in keywords)


def _default_recorder(group: str | None, entry: dict[str, Any]) -> bool:
    """Persist an overheard line only inside the isolated trial root.

    Fail-closed: any root other than the trial root, or a missing store module,
    records nothing. The listening switch itself lives in the store.
    """
    if os.environ.get("ASTRBOT_ROOT", "") != TRIAL_ROOT or not group:
        return False
    from naiwa_store import Store

    return Store(pathlib.Path(TRIAL_ROOT) / "data" / "napcat-trial").record_group(group, entry)


def _integer(value: Any) -> int | None:
    """Parse a JSON integer (or signed decimal string) without bool coercion."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value and value[0] in "-0123456789":
        digits = value[1:] if value.startswith("-") else value
        if digits and all("0" <= char <= "9" for char in digits):
            return int(value)
    return None


def _allowed_groups(policy: dict[str, Any]) -> tuple[str, ...]:
    """Every authorized group id. ``test_group_id`` stays supported for configs
    written before multiple groups existed."""
    found: list[str] = []
    single = _nonempty(policy.get("test_group_id"))
    if single:
        found.append(single)
    listed = policy.get("group_ids")
    if isinstance(listed, list):
        for value in listed[:8]:
            group = _nonempty(value)
            if group and group not in found:
                found.append(group)
    return tuple(found)


def inspect_event(event: Any, *, policy: dict[str, Any]) -> GateResult:
    """Authorize one event before native conversion; never retain raw payloads.

    The expected bot identity, owner identity and single test group must be
    exact decimal strings in a private policy file. Group text that does not @
    this bot becomes an ``observe`` outcome: it is handed to the recorder and
    never reaches the model, memory or native conversion. Unsupported segments
    in a message that does @ the bot become a fixed notice outcome.
    """
    if not isinstance(event, dict) or not isinstance(policy, dict):
        return GateResult("deny", reason="malformed")

    self_id = _nonempty(event.get("self_id"))
    expected_self = _nonempty(policy.get("self_id"))
    owner = _nonempty(policy.get("owner_id"))
    groups = _allowed_groups(policy)
    if not all((self_id, expected_self, owner)) or not groups:
        return GateResult("deny", reason="policy_or_identity_missing")
    if self_id != expected_self:
        return GateResult("deny", reason="self_id_mismatch")
    if event.get("post_type") != "message":
        return GateResult("ignore", reason="non_message_event")

    sender = _nonempty(event.get("user_id"))
    if sender is None:
        return GateResult("deny", reason="sender_identity_missing_or_invalid")
    if sender == self_id:
        return GateResult("ignore", reason="self_message")
    message_type = event.get("message_type")
    if message_type == "private":
        if sender != owner:
            return GateResult("deny", reason="private_not_allowlisted")
        kind = "private"
        group_id = None
    elif message_type == "group":
        group_id = _nonempty(event.get("group_id"))
        if group_id not in groups:
            return GateResult("deny", reason="group_not_allowlisted")
        kind = "group"
    else:
        return GateResult("deny", reason="unsupported_message_type")

    segments = event.get("message")
    delegated_group = kind == "group" and policy.get("group_chat_owner") == "chat_plus"
    if not isinstance(segments, list):
        if kind == "group":
            return GateResult("deny", reason="group_mention_unverifiable")
        return GateResult("fixed_reply", kind=kind, reason="malformed_message_segments")

    parts: list[str] = []
    images: list[str] = []
    mentioned = False
    unsupported = False
    for segment in segments:
        if not isinstance(segment, dict):
            unsupported = True
            continue
        segment_type = segment.get("type")
        data = segment.get("data")
        if not isinstance(data, dict):
            unsupported = True
            continue
        if segment_type == "text":
            text = data.get("text")
            if not isinstance(text, str):
                unsupported = True
            else:
                parts.append(text)
        elif segment_type == "image":
            if len(images) < MAX_IMAGES_PER_MESSAGE:
                reference = _image_reference(data)
                if reference is None:
                    unsupported = True
                else:
                    images.append(reference)
        elif segment_type == "at":
            target = _nonempty(data.get("qq"))
            if target == self_id:
                mentioned = True
            # @ 其他人或 @ 全体成员 与这条消息能不能处理无关，静默跳过。
        elif segment_type == "reply":
            # 引用只被当作上下文；我们不回读被引用的消息，正文照常回答。
            pass
        else:
            # file/record/video/forward/json and unknown types are never passed
            # into the native converter or any download path.
            unsupported = True

    keyword_triggered = kind == "group" and _keyword_hit("".join(parts), policy)
    if kind == "group" and unsupported and not mentioned and (delegated_group or not keyword_triggered):
        # Do not pass unhandled native segments to either AstrBot or chat_plus.
        # Authorized groups are delegated below only when every segment is in
        # the supported text/image/@/reply set.
        return GateResult("ignore", reason="unsupported_group_segment")
    if kind == "group" and not delegated_group and not mentioned and not keyword_triggered:
        overheard = "".join(parts).strip()
        if images:
            marker = "[图片]" if len(images) == 1 else f"[图片×{len(images)}]"
            overheard = (overheard + " " + marker).strip() if overheard else marker
        if not overheard:
            return GateResult("ignore", reason="empty_text")
        return GateResult("observe", kind=kind, text=overheard, reason="group_without_bot_mention")
    if unsupported:
        return GateResult("fixed_reply", kind=kind, reason="unsupported_segment")
    text = "".join(parts).strip()
    if not text and not images:
        if kind == "group" and mentioned and delegated_group:
            return GateResult("accept", kind=kind, text="", reason="group_delegated", mentioned=True)
        if kind == "group" and mentioned and not delegated_group:
            # People often send "@bot" and then type the real question in a
            # separate message. Answering immediately would double-reply, so
            # the caller waits briefly for a follow-up first.
            return GateResult("mention_wait", kind=kind, reason="mention_without_content")
        return GateResult("ignore", reason="empty_text")
    # Do not pass an unaddressed group image to the native image downloader.
    # chat_plus receives a text marker but can still use the surrounding text.
    if delegated_group and not mentioned and not keyword_triggered and images:
        marker = "[图片]" if len(images) == 1 else f"[图片×{len(images)}]"
        text = (text + " " + marker).strip() if text else marker
        images = []
    # Group wake/suppression policy belongs to chat_plus in delegated mode.
    reason = "group_delegated" if delegated_group else (
        "keyword_triggered" if keyword_triggered and not mentioned else "authorized_text"
    )
    return GateResult("accept", kind=kind, text=text, reason=reason, images=tuple(images), mentioned=mentioned)


def _safe_event_payload(event: dict[str, Any], result: GateResult) -> dict[str, Any]:
    """Build a new minimal OneBot event; never pass the original event onward."""
    sender = _nonempty(event.get("user_id"))
    self_id = _nonempty(event.get("self_id"))
    message_id = _integer(event.get("message_id"))
    if not all((sender, self_id)) or message_id is None:
        raise ValueError("authorized event lacks required routing metadata")
    chain: list[dict[str, Any]] = []
    if result.reason == "group_delegated" and result.mentioned:
        # Retain only an @ verified to target this bot; discard all other @s.
        chain.append({"type": "at", "data": {"qq": self_id}})
    chain.append({"type": "text", "data": {"text": result.text}})
    chain.extend({"type": "image", "data": {"file": reference, "url": reference}} for reference in result.images)
    payload: dict[str, Any] = {
        "post_type": "message",
        "message_type": result.kind,
        "self_id": self_id,
        "user_id": sender,
        "message_id": message_id,
        "time": int(event.get("time", 0)) if str(event.get("time", "0")).isdecimal() else 0,
        "message": chain,
        "raw_message": result.text,
        # AstrBot echoes sender.nickname into a <system_reminder> that reaches the
        # model, so a placeholder label here would be presented as the real name.
        "sender": {
            "user_id": int(sender),
            "nickname": (_display_name(event) or "成员" + _member_tag(_nonempty(event.get("group_id")), sender)[:6]
                         if result.reason == "group_delegated" else ""),
            "card": "",
        },
    }
    if result.kind == "group":
        group_id = _nonempty(event.get("group_id"))
        if not group_id:
            raise ValueError("authorized group event lacks group identity")
        payload["group_id"] = int(group_id)
    return payload


def install_guard(adapter_module: Any, *, policy: dict[str, Any], audit: Any,
                  record: Any = _default_recorder) -> None:
    """Wrap native conversion before any event reaches its raw logger/actions.

    ``audit`` must accept only fixed metadata fields and must never serialize
    arguments or event objects. ``record`` receives an already-minimized
    observation for overheard group text; it is the only sink for that text.
    The adapter's raw-message debug logger is disabled because even an
    authorized message body is private content.
    """
    adapter_cls = adapter_module.AiocqhttpAdapter
    native_convert = adapter_cls.convert_message

    async def _emit(self: Any, event: Any, result: GateResult) -> Any:
        payload = _safe_event_payload(event, result)
        event_type = type(event)
        safe_event = event_type.from_payload(payload) if hasattr(event_type, "from_payload") else payload
        converter_event = safe_event
        restore_verified_mention = result.reason == "group_delegated" and result.mentioned
        if restore_verified_mention:
            # The native aiocqhttp converter resolves every At through
            # get_group_member_info before creating AstrBot's At component.
            # That metadata action is intentionally denied by the trial's
            # outbound guard. Strip only the already-verified bot @ for native
            # conversion, then restore its native component below.
            converter_payload = dict(payload)
            converter_payload["message"] = [
                segment for segment in payload["message"]
                if not (isinstance(segment, dict) and segment.get("type") == "at")
            ]
            converter_event = (
                event_type.from_payload(converter_payload)
                if hasattr(event_type, "from_payload") else converter_payload
            )
        converted = await native_convert(self, converter_event)
        if converted is not None:
            # Preserve only the newly constructed minimal event in any
            # later AstrBot event or archive path.
            converted.raw_message = safe_event
            if restore_verified_mention:
                from astrbot.api.message_components import At
                # AstrBot events expose the chain only through get_messages();
                # inserting into a non-existent ``message`` attribute raised
                # inside the adapter and dropped every group @ mention.
                chain = (converted.get_messages() if hasattr(converted, "get_messages")
                         else getattr(converted, "message", None))
                if not isinstance(chain, list):
                    raise RuntimeError("native converter returned no AstrBot message chain")
                chain.insert(0, At(qq=str(payload["self_id"]), name=""))
            delegated_group = result.reason == "group_delegated"
            if result.reason in {"keyword_triggered", "mention_followup", "mention_after_text"}:
                # AstrBot's WakingCheckStage normally requires an @ for group
                # messages. Delegated messages retain the validated @ segment;
                # ordinary group chatter must not wake native fallback.
                converted._napcat_trial_force_wake = True
            sender = _nonempty(safe_event.get("user_id"))
            group = _nonempty(safe_event.get("group_id")) if result.kind == "group" else None
            converted._napcat_trial_state = {
                "kind": result.kind,
                "group_delegated": delegated_group,
                "mentioned": result.mentioned,
                "sender": sender,
                "group": group,
                "key": f"grp:{group}:{sender}" if group else f"c2c:{sender}",
                "speaker": _display_name(event),
                "member_tag": _member_tag(group, sender),
                "event_time_ms": _event_time_ms(event),
                "question": result.text,
                "images": list(result.images),
                "message_id": _integer(event.get("message_id")),
                "received_at": time.monotonic(),
                "sent": False,
                "model_answer": False,
                "answer": "",
                "committed": False,
                "audit": audit,
            }
        audit("accepted", kind=result.kind, reason=result.reason)
        return converted

    async def guarded_convert(self: Any, event: Any) -> Any:
        try:
            result = inspect_event(event, policy=policy)
        except Exception:
            audit("gate_error")
            return None
        if result.decision == "deny":
            audit("denied", reason=result.reason)
            return None
        if result.decision == "ignore":
            audit("ignored", reason=result.reason)
            return None
        try:
            message_id = _integer(event.get("message_id"))
            if message_id is None:
                audit("denied", reason="message_id_missing_or_invalid")
                return None
            event_key = (_nonempty(event.get("self_id")) or "", message_id)
            now = time.monotonic()
            for key, seen_at in list(_SEEN_EVENTS.items()):
                if now - seen_at > 600:
                    _SEEN_EVENTS.pop(key, None)
            if event_key in _SEEN_EVENTS:
                audit("ignored", reason="duplicate_event")
                return None
            _SEEN_EVENTS[event_key] = now
            while len(_SEEN_EVENTS) > _MAX_SEEN_EVENTS:
                oldest = min(_SEEN_EVENTS, key=_SEEN_EVENTS.get)
                _SEEN_EVENTS.pop(oldest, None)
        except Exception:
            audit("gate_error")
            return None
        if result.decision == "mention_wait":
            sender = _nonempty(event.get("user_id"))
            group = _nonempty(event.get("group_id"))
            if not sender or not group:
                audit("ignored", reason="empty_text")
                return None
            slot = (group, sender)
            pending = _PENDING_ACK.pop(slot, None)
            if pending is not None:
                pending.cancel()
            # "先发问题，再 @" is the mirror image of "@ 完再打字": the text
            # already arrived and was only archived, so reuse it as the prompt.
            recent = _RECENT_GROUP_TEXT.get(slot)
            if recent is not None and time.monotonic() - recent[0] <= _RECENT_TEXT_TTL:
                _RECENT_GROUP_TEXT.pop(slot, None)
                audit("accepted", kind="group", reason="mention_after_text")
                remainder = GateResult("accept", kind="group", text=recent[1],
                                       reason="mention_after_text")
                return await _emit(self, event, remainder)
            _RECENT_GROUP_TEXT.pop(slot, None)

            async def ack_later() -> None:
                try:
                    await asyncio.sleep(MENTION_WAIT_SECONDS)
                except asyncio.CancelledError:
                    return
                _PENDING_ACK.pop(slot, None)
                token = FIXED_NOTICE_ACTIVE.set(True)
                try:
                    response = await self.bot.call_action(
                        "send_msg", message_type="group", group_id=int(group), message=MENTION_ONLY_REPLY)
                    okay = isinstance(response, dict) and _integer(response.get("message_id")) is not None
                    audit("mention_ack_sdk_success" if okay else "mention_ack_result_unconfirmed", kind="group")
                except Exception:
                    audit("mention_ack_action_error", kind="group")
                finally:
                    FIXED_NOTICE_ACTIVE.reset(token)

            _PENDING_ACK[slot] = asyncio.ensure_future(ack_later())
            audit("mention_without_content", kind=result.kind, reason="waiting")
            return None
        # A follow-up from the same member inside the wait window is the rest of
        # a split "@bot" + text pair, so it upgrades to a normal answer.
        slot = (result.kind == "group" and _nonempty(event.get("group_id")), _nonempty(event.get("user_id")))
        continuation = False
        if slot[0] and slot[1]:
            continuation = slot in _PENDING_ACK
            pending = _PENDING_ACK.pop(slot, None)
            if pending is not None:
                pending.cancel()
        if result.decision == "observe" and continuation:
            result = GateResult("accept", kind=result.kind, text=result.text,
                                reason="mention_followup", images=result.images)
        if result.decision == "observe":
            sender = _nonempty(event.get("user_id"))
            group = _nonempty(event.get("group_id"))
            try:
                recorded = bool(record(group, {
                    "t": _event_time_ms(event) or int(time.time() * 1000),
                    "member": _member_tag(group, sender),
                    "name": _display_name(event),
                    "text": result.text[:300],
                }))
            except Exception:
                audit("gate_error")
                return None
            audit("observed", kind=result.kind, reason="recorded" if recorded else "listen_off")
            if slot[0] and slot[1] and result.text and not result.text.startswith("["):
                _RECENT_GROUP_TEXT[slot] = (time.monotonic(), result.text[:300])
                while len(_RECENT_GROUP_TEXT) > _MAX_RECENT_TEXT:
                    _RECENT_GROUP_TEXT.pop(next(iter(_RECENT_GROUP_TEXT)))
            return None
        if result.decision == "fixed_reply":
            notice_token = FIXED_NOTICE_ACTIVE.set(True)
            try:
                action: dict[str, Any] = {"message": UNSUPPORTED_REPLY}
                if result.kind == "group":
                    action.update(message_type="group", group_id=int(event["group_id"]))
                else:
                    action.update(message_type="private", user_id=int(event["user_id"]))
                response = await self.bot.call_action("send_msg", **action)
                # aiocqhttp 1.4.4 validates status/retcode internally and
                # returns the OneBot ``data`` object, not the raw WS envelope.
                okay = isinstance(response, dict) and _integer(response.get("message_id")) is not None
                audit("fixed_notice_sdk_success" if okay else "fixed_notice_result_unconfirmed", kind=result.kind)
            except Exception:
                audit("fixed_notice_action_error", kind=result.kind)
            finally:
                FIXED_NOTICE_ACTIVE.reset(notice_token)
            return None
        try:
            return await _emit(self, event, result)
        except Exception:
            audit("conversion_error", kind=result.kind)
            return None

    # This exact module logger call emits the entire event at DEBUG level.
    # Suppress adapter debug logging so no accepted message body reaches logs.
    if hasattr(adapter_module, "logger"):
        adapter_module.logger.debug = lambda *args, **kwargs: None
    adapter_cls.convert_message = guarded_convert

    native_create_event = getattr(adapter_cls, "create_event", None)
    if native_create_event is not None:
        def create_event(self: Any, message: Any) -> Any:
            event = native_create_event(self, message)
            if getattr(message, "_napcat_trial_force_wake", False):
                event.is_at_or_wake_command = True
                event.is_wake = True
            return event

        adapter_cls.create_event = create_event
