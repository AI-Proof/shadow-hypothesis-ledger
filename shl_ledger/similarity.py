"""Cheap, dependency-free text matching for SHL.

Used for two jobs:
  1. ghost detection: does a user turn (or a memory write) talk about a parked guess?
  2. re-derivation guard: is a "new" guess really a parked guess with a new id?

This is a heuristic, not semantic understanding. It catches exact and near-exact
wording (reordered words, case, punctuation, small edits). It will miss true
paraphrases that share few content words. Say so wherever it is relied on.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# Small English stopword list. Deliberately short: we only want to drop words
# that carry no claim ("the", "is", "to"), not words like "not" that flip one.
_STOP = frozenset(
    """a an the and or but if of to in on at for from by with as is are was were be been being
    it its this that these those there here he she they them his her their you your i me my we our
    has have had do does did will would can could should may might must very really just also""".split()
)
_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(_WORD.findall(text.lower()))


def _stem(w: str) -> str:
    """Very light suffix stripping so "prefers"/"prefer", "meetings"/"meeting" match."""
    for suf in ("ing", "ies", "ed", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokens(text: str) -> set[str]:
    """Content words, lightly stemmed. Numbers always count ("2 kids" is not "3 kids")."""
    return {_stem(w) for w in normalize(text).split() if w not in _STOP and (len(w) > 1 or w.isdigit())}


def text_hash(text: str) -> str:
    """Hash of the normalized text. Lets us recognise a guess after its text is erased."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def containment(needle: str, haystack: str) -> float:
    """Share of the needle's content words that appear in the haystack (0..1)."""
    n = tokens(needle)
    if not n:
        return 0.0
    return len(n & tokens(haystack)) / len(n)


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def overlaps(parked_text: str, other: str, threshold: float = 0.75) -> bool:
    """True if `other` mentions the parked guess closely enough to treat it as a ghost.

    Exact normalized substring always counts. Otherwise most of the parked guess's
    content words must appear in `other`. Guesses with fewer than 3 content words
    only match exactly, to avoid false alarms on short phrases.
    """
    if not parked_text or not other:
        return False
    if normalize(parked_text) and normalize(parked_text) in normalize(other):
        return True
    if len(tokens(parked_text)) < 3:
        return False
    return containment(parked_text, other) >= threshold


def same_guess(a: str, b: str, threshold: float = 0.8) -> bool:
    """True if two guesses are (near-)identical. Used to stop re-staging under a new id."""
    if not a or not b:
        return False
    if normalize(a) == normalize(b):
        return True
    return jaccard(a, b) >= threshold
