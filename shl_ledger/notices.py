"""What the person sees from SHL: the frame, the session note, and short confirmations.

Everything here goes into the conversation (Hermes saves it in the chat history), so it
never contains the text of a rejected assumption. Counts and numbers only. The texts
are shown by `/shl`, whose output goes to the person alone.
"""
from __future__ import annotations

from typing import Iterable, Optional

FRAME_TOP = "░▒▓█ ᯽ SHL ᯽ █▓▒░"
FRAME_BOTTOM = "░▒▓███• ❉ •███▓▒░"


def frame(body: str) -> str:
    """Wrap an SHL section. Blank lines keep the frame clear of the text around it."""
    return f"{FRAME_TOP}\n\n{body.strip()}\n\n{FRAME_BOTTOM}"


def _n(k: int, one: str, many: Optional[str] = None) -> str:
    return f"{k} {one if k == 1 else (many or one + 's')}"


def _verb(k: int, one: str, many: str) -> str:
    return one if k == 1 else many


def session_note(s: dict) -> str:
    """The note at the start of a new session, from ledger.session_summary()."""
    lines: list[str] = []
    thr = f"{s.get('threshold', 0.65):.2f}".rstrip("0").rstrip(".")
    days = s.get("timed_days") or 90

    if s.get("upgraded_from"):
        old = {"v1": "the technical preview", "v2": "v0.2"}.get(s["upgraded_from"], s["upgraded_from"])
        lines.append(f"Your ledger was upgraded from {old} to v0.3. A backup was saved in ledger/backups/.")
    if not s.get("log_ok", True):
        lines.append(
            "Warning: the SHL change log was edited outside SHL. Your assumptions are still protected, "
            'but the history can\'t be trusted. Type "/shl verify" for details.'
        )

    mode = s.get("mode", "on")
    parked, purged = s.get("parked", 0), s.get("purged", 0)

    if mode == "off":
        lines.append(
            f"{_n(parked, 'assumption')} you rejected {_verb(parked, 'is', 'are')} parked and kept out of every "
            "prompt. Your messages aren't being checked against them (evidence is off), so none will be "
            "brought back or purged."
        )
    elif mode == "log":
        lines.append(
            "Trial mode: SHL is watching but changing nothing. Since your last session it would have "
            f"brought back {_n(s.get('would_notice', 0), 'assumption')} and purged {s.get('would_erase', 0)}."
        )
        lines.append(
            f'{_n(parked, "assumption")} {_verb(parked, "is", "are")} parked. Type "/shl report" to see what it '
            'found; switch it on with evidence_mode: "on" when you\'re happy.'
        )
    else:
        new_ev, new_t, old = s.get("new_evidence", 0), s.get("new_timed", 0), s.get("still_waiting", 0)
        news = []
        if new_ev:
            news.append(
                f"{_n(new_ev, 'assumption')} you once rejected {_verb(new_ev, 'has', 'have')} crossed the "
                f"confidence threshold ({thr})"
            )
        if new_t:
            news.append(
                f"{_n(new_t, 'assumption')} you rejected {_verb(new_t, 'is', 'are')} due for {_verb(new_t, 'its', 'their')} "
                f"{days}-day check"
            )
        if news:
            lines.append(
                "Since your last session: " + " and ".join(news) + f", and {_verb(new_ev + new_t, 'is', 'are')} "
                "waiting for your reconsideration."
            )
        if old:
            d = s.get("still_waiting_days", 0)
            when = "earlier today" if d < 1 else f"{_n(d, 'day')} ago"
            lines.append(f"Still waiting from before: {_n(old, 'assumption')} (first mentioned {when}).")
        if purged:
            prefix = "" if news else "Since your last session: "
            lines.append(
                f"{prefix}{_n(purged, 'assumption')} quietly decayed and {_verb(purged, 'was', 'were')} purged."
            )
        if news or old:
            lines.append(
                'Type "/shl" to see them and decide. Until you do, nothing changes and the assistant won\'t use them.'
            )
        elif parked:
            lines.append(
                f"{_n(parked, 'assumption')} you rejected {_verb(parked, 'is', 'are')} parked in your "
                "Shadow-Hypothesis Ledger. No actions necessary."
            )
            lines.append('(type "/shl all" to see all SHL assumptions)')
        elif s.get("ever"):
            lines.append("No rejected assumptions are parked right now. No actions necessary.")
        else:
            lines.append("SHL is active. You haven't rejected any assumptions yet.")
            lines.append(
                "When the assistant assumes something about you that's wrong, just say so. It will be parked "
                "here and never used unless you decide otherwise."
            )

    if s.get("no_rule"):
        k = s["no_rule"]
        fix = '"/shl rule ALL auto"' + (', then "/shl rename ALL"' if s.get("old_ids") else "")
        lines.append(
            f"{_n(k, 'older assumption')} {_verb(k, 'has', 'have')} no evidence rule yet, so SHL can't track "
            f"{_verb(k, 'it', 'them')}. To fix: type {fix}."
        )
    elif s.get("old_ids"):
        lines.append(
            f'{_n(s["old_ids"], "parked assumption")} still {_verb(s["old_ids"], "has", "have")} an old, descriptive id. '
            'Type "/shl rename ALL" to give them neutral ones.'
        )
    return frame("\n".join(lines))


FAILED_NOTE = frame(
    "SHL couldn't read its ledger at the start of this session. Assumptions you rejected live only in the "
    "ledger, so they still can't reach the prompt, but SHL may not be tracking this session. "
    'If this keeps happening, type "/shl verify".'
)


# --------------------------------------------------------------------------- confirmations


def _nums(labels: Iterable) -> str:
    return ", ".join(str(x) for x in labels)


def parked(labels: list) -> str:
    if len(labels) == 1:
        n = str(labels[0]).lstrip("#")
        return (
            f"Parked 1 assumption you rejected ({labels[0]}). It won't be used.\n"
            f'Changed your mind? "/shl use {n}"'
        )
    return (
        f"Parked {len(labels)} assumptions you rejected ({_nums(labels)}). They won't be used.\n"
        'Changed your mind? "/shl use NUMBER"'
    )


def erased_weak(threshold: float) -> str:
    return (
        f"That assumption was weak (confidence below {threshold:.2f}), so it was erased right away. "
        'The assistant can\'t add it again; you still can ("/shl add").'
    )


def blocked(label: str, word: str) -> str:
    n = str(label).lstrip("#")
    return (
        f"The assistant tried to {word} {label} without you. Only you can decide: "
        f'"/shl use {n}", "/shl later {n}" or "/shl drop {n}".'
    )


def readd_blocked(labels: list) -> str:
    return f"The assistant tried to add back an assumption you rejected ({_nums(labels)}). It was blocked."


def decided(label: str, action: str, days: int = 90) -> str:
    if action == "REVIEW":
        return f"Restored {label} at your request. The assistant may use it again."
    if action == "DEFER":
        when = f"in {_n(days, 'day')}" if days else "only when new evidence appears"
        return f"Kept {label} parked at your request. It will be checked again {when}."
    return f"Erased {label} at your request."
