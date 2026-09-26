"""Screen text before it becomes memory, a summary or a prompt.

The prompt gate only controls the SHL block. A rejected guess can still slip back
through other doors: a memory file the agent writes, a session summary, a pasted
transcript. `screen()` checks a piece of text against the parked guesses and
replaces sentences that repeat one with a marker, so the text can be stored
without carrying the rejected guess.

Heuristic (see similarity.py): catches exact and near-exact wording, not
paraphrase. Use it as a seatbelt, not a guarantee.
"""
from __future__ import annotations

import re

from . import ledger, similarity

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_SECRET = re.compile(r"(api[_-]?key|secret|password|token|bearer)\s*[:=]\s*\S+", re.I)


def ghost_ids(text: str) -> list[str]:
    """Ids of parked guesses that `text` repeats. Never returns the guess itself."""
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
    screened = " ".join(out_parts)
    screened, n_secrets = _SECRET.subn(lambda m: m.group(1) + "=[removed]", screened)
    return {"text": screened, "parked_hits": hits, "secrets_removed": n_secrets}
