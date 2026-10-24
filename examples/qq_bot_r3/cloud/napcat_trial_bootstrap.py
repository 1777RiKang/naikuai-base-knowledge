"""Start a restricted AstrBot instance rooted only in the trial directory."""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import sys
import asyncio
import time
from logging.handlers import RotatingFileHandler

TRIAL_ROOT = pathlib.Path("/var/lib/astrbot-naiwa-trial")
POLICY_PATH = TRIAL_ROOT / "data" / "napcat-trial" / "policy.json"
CONFIG_PATH = TRIAL_ROOT / "data" / "cmd_config.json"
PERSONA_PATH = TRIAL_ROOT / "PERSONA.md"
SITE_ROOT = pathlib.Path(__file__).resolve().parent
os.environ["ASTRBOT_ROOT"] = str(TRIAL_ROOT)
sys.path.insert(0, str(SITE_ROOT))


def _private_json(path: pathlib.Path) -> dict:
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError("trial private file owner/mode check failed")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("trial private file shape check failed")
    return value


def _preflight() -> dict:
    if pathlib.Path(os.environ["ASTRBOT_ROOT"]).resolve() != TRIAL_ROOT.resolve():
        raise RuntimeError("trial root mismatch")
    policy = _private_json(POLICY_PATH)
    config = _private_json(CONFIG_PATH)
    persona_info = PERSONA_PATH.lstat()
    if (
        PERSONA_PATH.is_symlink()
        or not PERSONA_PATH.is_file()
        or persona_info.st_uid != os.geteuid()
        or persona_info.st_mode & 0o077
    ):
        raise RuntimeError("trial persona file owner/mode check failed")
    persona = PERSONA_PATH.read_text(encoding="utf-8-sig").strip()
    if not persona or "奶蛙" not in persona:
        raise RuntimeError("trial persona content check failed")
    for key in ("self_id", "owner_id", "test_group_id"):
        if not isinstance(policy.get(key), str) or not re.fullmatch(r"[0-9]{1,20}", policy[key]):
            raise RuntimeError("trial identity policy is incomplete")
    extra = policy.get("group_ids")
    if extra is not None and (
        not isinstance(extra, list)
        or len(extra) > 8
        or not all(isinstance(item, str) and re.fullmatch(r"[0-9]{1,20}", item) for item in extra)
    ):
        raise RuntimeError("trial group id list is malformed")
    group_owner = policy.get("group_chat_owner", "legacy")
    if group_owner not in {"legacy", "chat_plus"}:
        raise RuntimeError("trial group chat owner policy is invalid")
    platform = config.get("platform")
    if not isinstance(platform, list) or len(platform) != 1:
        raise RuntimeError("trial must have exactly one platform")
    adapter = platform[0]
    if (
        adapter.get("type") != "aiocqhttp"
        or adapter.get("enable") is not True
        or adapter.get("ws_reverse_host") != "127.0.0.1"
        or adapter.get("ws_reverse_port") != 16199
        or not isinstance(adapter.get("ws_reverse_token"), str)
        or len(adapter["ws_reverse_token"]) < 40
    ):
        raise RuntimeError("trial OneBot listener configuration failed closed")
    if config.get("dashboard", {}).get("host") != "127.0.0.1" or str(config.get("dashboard", {}).get("port")) != "6186":
        raise RuntimeError("trial dashboard must be loopback-only on 6186")
    if config.get("wake_prefix") != [""]:
        raise RuntimeError("trial wake policy must defer to the pre-convert gate")
    expected_plugins = ["napcat_trial"]
    if group_owner == "chat_plus":
        expected_plugins.append("astrbot_plugin_group_chat_plus")
    expected_plugins.append("astrbot_plugin_naikuai_intake")
    if config.get("disable_builtin_commands") is not True or config.get("plugin_set") != expected_plugins:
        raise RuntimeError("trial command/plugin allowlist check failed")
    if config.get("log_file_enable") is not False or config.get("trace_enable") is not False or config.get("trace_log_enable") is not False:
        raise RuntimeError("trial raw log outputs must be disabled")
    settings = config.get("platform_settings", {})
    if settings.get("friend_message_needs_wake_prefix") is not False or settings.get("ignore_at_all") is not True:
        raise RuntimeError("trial platform wake settings check failed")
    if settings.get("reply_with_mention") is not False or settings.get("reply_with_quote") is not False:
        raise RuntimeError("trial replies must remain plain text without native mention/quote decoration")
    return policy, config


def _audit_logger() -> logging.Logger:
    directory = TRIAL_ROOT / "data" / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    logger = logging.getLogger("napcat_trial_metadata")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(directory / "trial-metadata.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    os.chmod(directory / "trial-metadata.log", 0o600)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def _redact_framework_logs(audit=None) -> None:
    factory = logging.getLogRecordFactory()

    def redacted(*args, **kwargs):
        record = factory(*args, **kwargs)
        exception_type = record.exc_info[0] if isinstance(record.exc_info, tuple) and record.exc_info else None
        if exception_type is None:
            exception_type = sys.exc_info()[0]
        if isinstance(exception_type, type):
            name = exception_type.__name__.lower()
            if audit and re.fullmatch(r"[a-z_]{1,64}", name):
                audit("framework_exception", reason=name)
        record.msg = "framework_event"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return record

    logging.setLogRecordFactory(redacted)
    try:
        from loguru import logger

        def redact_loguru(record):
            exception = record.get("exception")
            exception_type = exception[0] if isinstance(exception, tuple) and exception else None
            if exception_type is None:
                exception_type = sys.exc_info()[0]
            if isinstance(exception_type, type):
                name = exception_type.__name__.lower()
                if audit and re.fullmatch(r"[a-z_]{1,64}", name):
                    audit("framework_exception", reason=name)
            record.update(message="framework_event", exception=None)

        logger.configure(patcher=redact_loguru)
    except Exception:
        pass


def _model_display_name(config: dict) -> str:
    """Footer label for ordinary answers; knowledge lookups use the KB footer."""
    label = os.environ.get("NAIKUAI_MODEL_LABEL", "").strip()
    if label:
        return label
    providers = config.get("provider")
    if isinstance(providers, list):
        for provider in providers:
            if isinstance(provider, dict) and provider.get("id") == "napcat-trial-model":
                model = provider.get("model")
                if isinstance(model, str) and model.strip():
                    return model.strip()
    return "deepseek-v4.1-flash"


QUEUE_NOTICE = "消息有点多，刚才那条没排上，请稍后再发一次。"


def make_guarded_send(original_send, current_state, model_name):
    """Keep AstrBot's native MessageChain send path, but allow text only."""
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Plain

    async def guarded_send(self, message):
        state = self.get_extra("_napcat_trial_state")
        if not isinstance(state, dict):
            raise RuntimeError("trial outbound send lacks authorized event")
        if state.get("send_entered") is True:
            raise RuntimeError("trial duplicate outbound attempt rejected")
        state["send_entered"] = True
        deadline = state.get("pipeline_start", state.get("received_at", time.monotonic())) + 90
        text = ""
        if isinstance(message, str):
            valid = True
            text = message
        else:
            valid = isinstance(message, MessageChain) and all(isinstance(part, Plain) for part in message.chain)
            if valid:
                text = "".join(part.text for part in message.chain)
        if time.monotonic() > deadline:
            state["fallback"] = True
            return None
        if state.get("timeout_notice"):
            state["fallback"] = True
            text = "本次试运行处理超时，请稍后重试。"
        elif state.get("queue_notice"):
            state["fallback"] = True
            text = QUEUE_NOTICE
        elif not state.get("model_answer") and not state.get("local_command"):
            state["fallback"] = True
            text = "本次试运行暂时无法完成，请稍后再试。"
        elif not valid:
            state["fallback"] = True
            text = "本次试运行暂时无法完成，请稍后再试。"
        api_key = os.environ.get("API_KEY")
        if api_key and api_key in text:
            text = text.replace(api_key, "[redacted]")
        text = text[:1200]
        if (
            state.get("model_answer")
            and not state.get("local_command")
            and not state.get("timeout_notice")
            and not state.get("queue_notice")
        ):
            if state.get("kb_used"):
                suffix = "\n—— 来自奶蛙的大肚肚"
            else:
                suffix = "\n—— " + model_name
            text = text[:1200-len(suffix)] + suffix
        safe_chain = MessageChain().message(text)
        token = current_state.set(state)
        try:
            return await original_send(self, safe_chain)
        finally:
            current_state.reset(token)

    return guarded_send


def main() -> None:
    os.umask(0o077)
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
    os.environ["XDG_RUNTIME_DIR"] = "/run/astrbot-naiwa-trial"
    policy, config = _preflight()
    metadata_logger = _audit_logger()

    def audit(event: str, **fields) -> None:
        if event not in {
            "accepted", "observed", "denied", "ignored", "gate_error", "conversion_error",
            "mention_without_content", "mention_ack_sdk_success", "mention_ack_result_unconfirmed",
            "mention_ack_action_error",
            "fixed_notice_sdk_success", "fixed_notice_result_unconfirmed", "fixed_notice_action_error",
            "outbound_sdk_success", "outbound_result_unconfirmed", "outbound_action_error",
            "request_prepared", "persona_config_rejected",
            "group_request_rejected", "group_handler_checked", "framework_exception", "action_rejected",
            "timeout_notice_suppressed", "pipeline_timeout", "queue_stale_notice",
            "queue_notice_suppressed", "context_capacity_rejected",
        }:
            return
        from datetime import datetime, timezone
        safe = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
        if fields.get("kind") in {"group", "private"}:
            safe["kind"] = fields["kind"]
        reason = fields.get("reason")
        if isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,64}", reason):
            safe["reason"] = reason
        if isinstance(fields.get("persona_name_present"), bool):
            safe["persona_name_present"] = fields["persona_name_present"]
        for name, limit in (("elapsed", 600), ("total_elapsed", 600), ("chars", 100000)):
            value = fields.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= limit:
                safe[name] = round(float(value), 1) if name != "chars" else int(value)
        metadata_logger.handle(logging.LogRecord("napcat_trial_metadata", logging.INFO, __file__, 0, json.dumps(safe), (), None))

    _redact_framework_logs(audit)
    from aiocqhttp.api_impl import UnifiedApi
    from astrbot.cli.__main__ import cli
    from astrbot.core.pipeline.scheduler import PipelineScheduler
    from astrbot.core.platform.sources.aiocqhttp import aiocqhttp_message_event, aiocqhttp_platform_adapter
    from napcat_guard import FIXED_NOTICE_ACTIVE, MENTION_ONLY_REPLY, UNSUPPORTED_REPLY, install_guard

    ready_marker = TRIAL_ROOT / "data" / "napcat-trial" / "plugin-ready.json"
    ready_marker.unlink(missing_ok=True)

    install_guard(aiocqhttp_platform_adapter, policy=policy, audit=audit)
    current_state = __import__("contextvars").ContextVar("napcat_trial_current_state", default=None)
    original_handle = aiocqhttp_platform_adapter.AiocqhttpAdapter.handle_msg
    concurrency = asyncio.Semaphore(4)
    queue_locks: dict[str, asyncio.Lock] = {}
    notice_last: dict[str, float] = {}
    queue_notice_last: dict[str, float] = {}
    QUEUE_WAIT_LIMIT = 15.0
    NOTICE_COOLDOWN = 60.0
    PIPELINE_BUDGET = float(os.environ.get("NAIKUAI_PIPELINE_BUDGET", "60"))

    async def guarded_handle(self, message):
        state = getattr(message, "_napcat_trial_state", None)
        event = self.create_event(message)
        if not isinstance(state, dict):
            audit("denied", reason="trial_state_missing")
            return
        event.set_extra("_napcat_trial_state", state)
        state["audit"] = audit
        state.setdefault("received_at", time.monotonic())
        self.commit_event(event)

    aiocqhttp_platform_adapter.AiocqhttpAdapter.handle_msg = guarded_handle
    original_adapter_run = aiocqhttp_platform_adapter.AiocqhttpAdapter.run

    def checked_adapter_run(self):
        marker = _private_json(ready_marker)
        if marker.get("ready") is not True or marker.get("pid") != os.getpid():
            raise RuntimeError("trial plugin readiness check failed")
        return original_adapter_run(self)

    aiocqhttp_platform_adapter.AiocqhttpAdapter.run = checked_adapter_run
    original_send = aiocqhttp_message_event.AiocqhttpMessageEvent.send

    aiocqhttp_message_event.AiocqhttpMessageEvent.send = make_guarded_send(original_send, current_state,
                                                                          _model_display_name(config))
    original_action = UnifiedApi.call_action

    async def guarded_action(self, action: str, **params):
        if action == "send_msg" and FIXED_NOTICE_ACTIVE.get():
            if params.get("message") not in {
                UNSUPPORTED_REPLY,
                MENTION_ONLY_REPLY,
                "本次试运行处理超时，请稍后重试。",
                QUEUE_NOTICE,
            }:
                raise RuntimeError("fixed notice action rejected")
            result = await original_action(self, action, **params)
            return result
        state = current_state.get()
        allowed_action = ("send_group_msg" if isinstance(state, dict) and state.get("kind") == "group" else "send_private_msg")
        target_key = "group_id" if allowed_action == "send_group_msg" else "user_id"
        expected_target = state.get("group") if target_key == "group_id" else state.get("sender") if isinstance(state, dict) else None
        text = params.get("message")
        text_only = isinstance(text, str)
        if isinstance(text, list):
            text_only = all(isinstance(segment, dict) and segment.get("type") == "text" and isinstance(segment.get("data", {}).get("text"), str) for segment in text)
        if (
            not isinstance(state, dict)
            or action != allowed_action
            or str(params.get(target_key)) != expected_target
            or (params.get("self_id") is not None and str(params.get("self_id")) != policy["self_id"])
            or not text_only
        ):
            allowed_diagnostic_apis = {
                "get_group_info", "get_group_member_info", "get_login_info",
                "get_stranger_info", "get_group_member_list", "get_msg", "get_status",
            }
            audit("action_rejected", reason=action if action in allowed_diagnostic_apis else "unknown_action")
            raise RuntimeError("trial OneBot action rejected")
        if state.get("attempted") is True:
            raise RuntimeError("trial duplicate action rejected")
        state["attempted"] = True
        result = await original_action(self, action, **params)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            state["sent"] = True
            audit("outbound_sdk_success", kind=state["kind"],
                  elapsed=round(time.monotonic() - state.get("prepared_at", state.get("received_at", time.monotonic())), 1),
                  total_elapsed=round(time.monotonic() - state.get("received_at", time.monotonic()), 1))
        else:
            state["sent"] = False
            audit("outbound_result_unconfirmed", kind=state["kind"])
        return result

    UnifiedApi.call_action = guarded_action

    original_execute = PipelineScheduler.execute

    async def bounded_execute(self, event):
        state = event.get_extra("_napcat_trial_state")
        if not isinstance(state, dict):
            return
        key = state.get("key")
        if not isinstance(key, str):
            return
        group = state.get("group") if state.get("kind") == "group" else None
        channel = group if isinstance(group, str) and group else key
        lock = queue_locks.setdefault(channel, asyncio.Lock())
        enqueued = time.monotonic()
        token = current_state.set(state)
        try:
            async with lock:
                started = time.monotonic()
                state["pipeline_start"] = started
                if started - enqueued > QUEUE_WAIT_LIMIT:
                    audit("group_request_rejected", kind=state.get("kind"), reason="queue_stale")
                    now = time.monotonic()
                    key = f"queue:{channel}"
                    last = queue_notice_last.get(key)
                    if last is not None and now - last < NOTICE_COOLDOWN:
                        audit("queue_notice_suppressed", kind=state.get("kind"), reason="cooldown")
                        return
                    queue_notice_last[key] = now
                    audit("queue_stale_notice", kind=state.get("kind"))
                    try:
                        state["queue_notice"] = True
                        await event.send(QUEUE_NOTICE)
                    except Exception:
                        audit("outbound_action_error", kind=state.get("kind"))
                    return
                try:
                    async with asyncio.timeout(PIPELINE_BUDGET):
                        async with concurrency:
                            await original_execute(self, event)
                except TimeoutError:
                    if state.get("attempted"):
                        return
                    now = time.monotonic()
                    last = notice_last.get(channel)
                    if last is not None and now - last < NOTICE_COOLDOWN:
                        audit("timeout_notice_suppressed", kind=state.get("kind"), reason="cooldown")
                        return
                    notice_last[channel] = now
                    audit("pipeline_timeout", kind=state.get("kind"))
                    try:
                        state["timeout_notice"] = True
                        await event.send("本次试运行处理超时，请稍后重试。")
                    except Exception:
                        audit("outbound_action_error", kind=state["kind"])
        finally:
            current_state.reset(token)

    PipelineScheduler.execute = bounded_execute
    cli.main(args=["run", "--port", "6186"], standalone_mode=False)


if __name__ == "__main__":
    main()
