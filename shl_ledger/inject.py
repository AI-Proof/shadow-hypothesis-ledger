"""Build the SHL block that goes in front of every model call.

ACTIVE text goes in. Parked guesses go in by id only, never by text. Scores
never go in.
"""
from __future__ import annotations

import os

from . import ledger, screen

MAX_INSIGHT = 400   # characters per ACTIVE guess
MAX_ACTIVE = 32     # ACTIVE guesses per block
MAX_PARKED_IDS = 64 # parked ids listed per block


def _clip(text: str, n: int = MAX_INSIGHT) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def ghost_ids(user_message: str) -> list[str]:
    """Kept for compatibility with the technical preview."""
    return screen.ghost_ids(user_message)


def build_context(user_message: str = "") -> str:
    ctx = ledger.compile_active_context()
    lines = []
    if os.environ.get("SHL_GATE_NOTE", "").strip().lower() in {"1", "true", "yes", "on"}:
        lines.append("SHL note: this block is a database filter, not proof that other context is clean.")
    lines += [
        "SHL GATE. Guesses about the person that may be used are listed as ACTIVE. "
        "Guesses the person rejected are listed by id only.",
        "Use only ACTIVE items. Do not guess at, reconstruct or bring back parked items. "
        "Only the person can restore one (reconsent REVIEW).",
        f"ACTIVE {ctx['active_count']}:",
    ]
    for hid, text in ctx["active"][:MAX_ACTIVE]:
        lines.append(f"- {hid}: {_clip(text)}")
    if ctx["active_count"] > MAX_ACTIVE:
        lines.append(f"- … {ctx['active_count'] - MAX_ACTIVE} more ACTIVE omitted")
    ids = [hid for hid, _ in ctx["quarantined"]]
    shown = ", ".join(ids[:MAX_PARKED_IDS]) if ids else "(none)"
    if len(ids) > MAX_PARKED_IDS:
        shown += f", … {len(ids) - MAX_PARKED_IDS} more"
    lines.append(f"PARKED {ctx['quarantined_count']} (ids only; not to be used): {shown}")
    ghosts = ghost_ids(user_message)
    if ghosts:
        # SHL_GHOST_MODE=ask (default): the agent may ask whether to restore.
        # SHL_GHOST_MODE=inert: never offer to restore; for rejections that must stay final.
        mode = os.environ.get("SHL_GHOST_MODE", "ask").strip().lower()
        if mode == "inert":
            tail = ". Treat it as inert. Do not use it and do not offer to restore it."
        else:
            tail = ". If the person is raising it themselves, ask whether they want to restore it; otherwise ignore it."
        lines.append("GHOST: the current message seems to repeat parked guess(es) " + ", ".join(ghosts) + tail)
    return "\n".join(lines)
