"""Screen text before it becomes memory, a summary or a prompt.

The prompt gate only controls the SHL block. A rejected assumption can still slip back
through other doors: a memory file the agent writes, a session summary, a pasted
transcript. `screen()` checks a piece of text against the parked assumptions and
replaces sentences that repeat one with a marker, so the text can be stored
without carrying the rejected assumption.

Heuristic (see similarity.py): catches exact and near-exact wording, not
paraphrase. Use it as a seatbelt, not a guarantee.
"""
from __future__ import annotations

import re

from . import ledger, similarity

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_SECRET = re.compile(r"(api[_-]?key|secret|password|token|bearer)\s*[:=]\s*\S+", re.I)


def ghost_ids(text: str) -> list[str]:
    """Ids of parked assumptions that `text` repeats. Never returns the assumption itself."""
    if not text:
        return []
    return [hid for hid, phrase in ledger.quarantine_strips() if similarity.overlaps(phrase, text)]


def screen(text: str) -> dict:
    """Return {'text': screened text, 'parked_hits': [ids], 'secrets_removed': n}."""
    parked = ledger.quarantine_strips()
    hits: list[str] = []
    out_parts = []
    for part in _SENTENCE.split(text or ""):
        if not part.strip():
            continue
        matched = [hid for hid, phrase in parked if similarity.overlaps(phrase, part)]
        if matched:
            hits.extend(h for h in matched if h not in hits)
            out_parts.append("[parked: " + ", ".join(matched) + "]")
        else:
            out_parts.append(part)
    screened = " ".join(out_parts) if hits else (text or "")
    screened, n_secrets = _SECRET.subn(lambda m: m.group(1) + "=[removed]", screened)
    return {"text": screened, "parked_hits": hits, "secrets_removed": n_secrets}


# Argument names that carry text about to be saved. Others are left alone: a memory
# tool's `old_text` must match an existing entry exactly, and `action`/`target` are labels.
SCREEN_KEYS = frozenset({"content", "new_text", "text", "note", "notes", "summary", "body", "value", "entry"})


def screen_args(args):
    """Screen the text-carrying values in a tool call's arguments (nested dicts and lists included).

    Only keys in SCREEN_KEYS are screened. Returns (new_args, parked_hits); new_args is
    None when nothing changed. Used to screen memory writes before they are saved.
    """
    hits: list[str] = []
    changed = False

    def walk(v, key=None):
        nonlocal changed
        if isinstance(v, str):
            if key not in SCREEN_KEYS:
                return v
            if len(v.strip()) < 3:
                return v
            out = screen(v)
            if out["parked_hits"] or out["secrets_removed"]:
                changed = True
                hits.extend(h for h in out["parked_hits"] if h not in hits)
                return out["text"]
            return v
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        return v

    new = walk(args if args is not None else {})
    return (new if changed else None), hits
