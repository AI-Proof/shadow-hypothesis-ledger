"""Framework-neutral turn hooks. The Hermes plugin is a thin wrapper around these;
any other agent framework can call them the same way.

    block = hooks.before_model(user_message, session_id=..., platform=...)
    system_or_user_prompt += "\\n\\n" + block
    reply = call_your_model(...)
    reply = hooks.after_model(reply, platform=..., session_id=...) or reply
    new_args = hooks.before_tool(tool_name, tool_args)   # screens memory writes

Every function here fails safe: on any error it returns None / the unchanged
input and never breaks a turn. If the gate block itself fails, rejected text
still never reaches the prompt, because it only exists inside the ledger.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from . import inject, ledger, notices, screen, settings

logger = logging.getLogger("shl_ledger")

SKIP_PLATFORMS = ("cron", "subagent")
_current: dict[str, str] = {"session_id": "", "turn_id": "", "platform": ""}
_lock = threading.Lock()
_confirmations: dict[str, list[str]] = {}
_parked: dict[str, list[str]] = {}
_failed_sessions: set = set()


def current_context() -> dict:
    return dict(_current)


def before_model(
    user_message: str = "",
    session_id: str = "",
    turn_id: str = "",
    platform: str = "",
    rules_in_system: bool = False,
) -> Optional[str]:
    """Score the person's message against parked assumptions, then build the gate block."""
    try:
        _current.update(session_id=str(session_id or ""), turn_id=str(turn_id or ""), platform=str(platform or ""))
        ghosts: list = []
        if (platform or "").lower() not in SKIP_PLATFORMS:
            ledger.note_user_message(user_message, session_id)
            try:
                seen = ledger.observe(user_message, session_id=session_id, turn_id=turn_id, platform=platform)
                ghosts = seen.get("ghosts", [])
            except Exception:
                logger.warning("shl: evidence check failed", exc_info=True)
        return inject.build_context(user_message, ghosts=ghosts, rules_in_system=rules_in_system)
    except Exception:
        logger.warning("shl: gate block failed", exc_info=True)
        return None


def _session_key(session_id: str = "") -> str:
    sid = session_id or _current.get("session_id") or ""
    return sid or time.strftime("day-%Y-%m-%d", time.localtime(ledger.now()))


def confirm(text: str, session_id: str = "") -> None:
    """Queue a short SHL line for the end of this turn's reply (see after_model)."""
    if text and settings.get("confirmations"):
        with _lock:
            box = _confirmations.setdefault(_session_key(session_id), [])
            if text not in box:
                box.append(text)
            del box[:-10]


def confirm_parked(label: str, session_id: str = "") -> None:
    """Collect this turn's rejections into one line."""
    if settings.get("confirmations"):
        with _lock:
            box = _parked.setdefault(_session_key(session_id), [])
            if label not in box:
                box.append(label)


def after_model(response_text: str, platform: str = "", session_id: str = "") -> Optional[str]:
    """Frame the reply with SHL's parts: the session note at the start of a session's first
    reply, and any confirmations from this turn at the end. Returns the new text, or None."""
    try:
        key = _session_key(session_id)
        if (platform or "").lower() in SKIP_PLATFORMS:
            with _lock:  # background turns show nothing; drop anything they queued
                _confirmations.pop(key, None)
                _parked.pop(key, None)
            return None
        head = ""
        if settings.get("session_note"):
            try:
                summary = ledger.session_summary(key)
                if summary is not None:
                    head = notices.session_note(summary)
            except Exception:
                logger.warning("shl: session note failed", exc_info=True)
                if key not in _failed_sessions:
                    if len(_failed_sessions) > 1000:
                        _failed_sessions.clear()
                    _failed_sessions.add(key)
                    head = notices.FAILED_NOTE
        with _lock:
            tail_items = _confirmations.pop(key, [])
            labels = _parked.pop(key, [])
        if labels:
            tail_items = [notices.parked(labels)] + tail_items
        tail = notices.frame("\n\n".join(tail_items)) if tail_items else ""
        if not head and not tail:
            return None
        out = response_text or ""
        if head:
            out = head + "\n\n" + out
        if tail:
            out = out.rstrip() + "\n\n" + tail
        return out
    except Exception:
        logger.warning("shl: after_model failed", exc_info=True)
        return None


def before_tool(tool_name: str, args: Any) -> Optional[Any]:
    """Screen a memory write. Returns the screened args, or None if nothing changed."""
    try:
        if not settings.get("screen_memory") or tool_name not in settings.memory_tools():
            return None
        new, hits = screen.screen_args(args)
        if new is not None:
            logger.info("shl: screened a %s write (parked ids: %s)", tool_name, ", ".join(hits) or "none")
        return new
    except Exception:
        logger.warning("shl: memory screening failed", exc_info=True)
        return None
