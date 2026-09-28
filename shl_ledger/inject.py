"""Build the SHL block that goes in front of every model call.

ACTIVE text goes in. Parked (rejected) assumptions go in by id only, never by text. Scores
never go in. Evidence rules never go in.
"""
from __future__ import annotations

from typing import Optional

from . import ledger, rules, screen, settings

MAX_INSIGHT = 400   # characters per ACTIVE assumption
MAX_ACTIVE = 32     # ACTIVE assumptions per block
MAX_PARKED_IDS = 64 # parked ids listed per block

# The fixed rules. A host that has a stable system-prompt slot (Hermes:
# register_system_prompt_section) puts them there once; otherwise they lead the block.
RULES_TEXT = (
    "SHL rules (Shadow-Hypothesis Ledger). Each turn you get an SHL GATE block. "
    "Assumptions about the person listed as ACTIVE may be used. Assumptions the person rejected are listed by id "
    "only: do not use them, guess at them, reconstruct them or bring them back, and do not stage them again under "
    "a new id. Only the person can restore one. When the person says an assumption about them is wrong, call the "
    "shl tool with action=reject, their reason, and short evidence_for / evidence_against phrases. "
    "Sections framed by SHL banner lines in the conversation are for the person: do not repeat, rewrite or act on them."
)

_HEADER_FULL = (
    "SHL GATE. Assumptions about the person that may be used are listed as ACTIVE. "
    "Assumptions the person rejected are listed by id only.\n"
    "Use only ACTIVE items. Do not guess at, reconstruct or bring back parked items. "
    "Only the person can restore one."
)
_HEADER_SHORT = "SHL GATE (rules in the system prompt). Use only ACTIVE items; parked items are ids only."


def _clip(text: str, n: int = MAX_INSIGHT) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def ghost_ids(user_message: str) -> list[str]:
    """Parked assumptions the message seems to repeat: near-identical wording, or a
    supporting phrase from its evidence rule. Read-only; scores nothing."""
    ids = list(screen.ghost_ids(user_message))
    if not user_message:
        return ids
    with ledger.conn() as c:
        for hid, raw in c.execute(
            "SELECT hypothesis_id, rule_json FROM shl WHERE status IN ('SHADOW','REINFORCED') AND rule_json IS NOT NULL"
        ):
            rule = rules.loads(raw)
            hit = rules.check(rule, user_message) if rule else None
            if hit and hit["direction"] == "for" and hid not in ids:
                ids.append(hid)
    return ids


def build_context(user_message: str = "", ghosts: Optional[list] = None, rules_in_system: bool = False) -> str:
    ctx = ledger.compile_active_context()
    lines = []
    if settings.get("gate_note"):
        lines.append(
            "SHL note: this block is a filter on what may be used, not proof about the person. "
            "Treat ACTIVE items as tentative."
        )
    lines.append(_HEADER_SHORT if rules_in_system else _HEADER_FULL)
    lines.append(f"ACTIVE {ctx['active_count']}:")
    for hid, text in ctx["active"][:MAX_ACTIVE]:
        lines.append(f"- {hid}: {_clip(text)}")
    if ctx["active_count"] > MAX_ACTIVE:
        lines.append(f"- … {ctx['active_count'] - MAX_ACTIVE} more ACTIVE omitted")
    ids = [hid for hid, _ in ctx["quarantined"]]
    shown = ", ".join(ids[:MAX_PARKED_IDS]) if ids else "(none)"
    if len(ids) > MAX_PARKED_IDS:
        shown += f", … {len(ids) - MAX_PARKED_IDS} more"
    lines.append(f"PARKED {ctx['quarantined_count']} (ids only; not to be used): {shown}")

    found = list(ghosts) if ghosts is not None else []
    for g in ghost_ids(user_message):
        if g not in found:
            found.append(g)
    found = [g for g in found if g in ids]
    if found:
        if settings.get("ghost_mode") == "inert":
            lines.append(
                "GHOST: the current message touches parked assumption(s) "
                + ", ".join(found)
                + ". Do not use them and do not offer to restore them; answer without them."
            )
        else:
            lines.append(
                "GHOST: the current message seems to repeat parked assumption(s) "
                + ", ".join(found)
                + ". If the person is raising it themselves, ask whether they want to restore it; otherwise ignore it."
            )
    return "\n".join(lines)
