"""Cheap, dependency-free text matching for SHL.

Used for two jobs:
  1. ghost detection: does a user turn (or a memory write) talk about a parked assumption?
  2. re-derivation guard: is a "new" assumption really a parked assumption with a new id?

rules.py builds the evidence checks on the same normalisation and stemming.

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
STOP = frozenset(
    """a an the and or but if of to in on at for from by with as is are was were be been being
    it its this that these those there here he she they them his her their you your i me my we our
    has have had do does did will would can could should may might must very really just also""".split()
)
_STOP = STOP  # v0.2 name
_WORD = re.compile(r"[^\W_]+", re.UNICODE)  # letters and digits in any script


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(_WORD.findall(text.casefold()))


def stem(w: str) -> str:
    """Very light suffix stripping so "prefers"/"prefer", "meetings"/"meeting" match."""
    for suf in ("ing", "ies", "ed", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokens(text: str) -> set[str]:
    """Content words, lightly stemmed. Numbers always count ("2 kids" is not "3 kids")."""
    return {stem(w) for w in normalize(text).split() if w not in STOP and (len(w) > 1 or w.isdigit())}


def text_hash(text: str) -> str:
    """Hash of the normalized text. Lets us recognise an assumption after its text is erased."""
    n = normalize(text)
    if not n:  # nothing but punctuation or symbols: hash the raw text instead of ""
        n = "raw:" + " ".join((text or "").split())
    return hashlib.sha256(n.encode("utf-8")).hexdigest()


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
    """True if `other` mentions the parked assumption closely enough to treat it as a ghost.

    Exact normalized substring always counts. Otherwise most of the parked assumption's
    content words must appear in `other`. Assumptions with fewer than 3 content words
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
    """True if two assumptions are (near-)identical. Used to stop re-staging under a new id."""
    if not a or not b:
        return False
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return " ".join(a.split()) == " ".join(b.split())
    if na == nb:
        return True
    return jaccard(a, b) >= threshold
