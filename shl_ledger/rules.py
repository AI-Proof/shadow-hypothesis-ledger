"""Evidence rules: plain-code checks that decide whether a message supports or
contradicts a parked assumption. No model is called here.

A rule belongs to one parked assumption:

    {"for":     ["vegetarian", "plant-based", "no meat"],
     "against": ["steak", "chicken", "ate meat"],
     "first_person": true}

How a message is checked, one sentence at a time:
  * A phrase of up to 3 content words must appear in order (at most 2 other words
    between them). A longer phrase matches if 75% of its content words appear.
  * A "for" phrase with a negation shortly before it ("not", "never", "don't",
    "no longer", Slovak "nie", "nikdy") counts as evidence AGAINST the assumption.
    A negated "against" phrase counts as nothing.
  * With first_person, a sentence that is clearly about someone else
    ("my friend is vegetarian") is skipped.

This is word matching, not understanding. It is meant to be cheap, local and
predictable; mistakes cost at most a notice, because nothing is ever restored
without the person.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from . import similarity

MAX_PHRASES = 20
MAX_PHRASE_CHARS = 60
MAX_EXCERPT = 120

_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")

def _wordset(text: str) -> frozenset:
    """Normalised the same way as messages (accents removed, any script kept)."""
    return frozenset(similarity.normalize(text).split())


# Negation words. English and Slovak, plus a basic set for Czech, Russian, Polish, Spanish,
# Portuguese, German and Italian. In other languages a negated statement may count as support.
NEGATORS = _wordset(
    """not no never nor none neither nobody nothing without cannot cant dont doesnt didnt isnt arent
    wasnt werent wont wouldnt shouldnt couldnt havent hasnt hadnt stopped quit
    nie ne nikdy žiadny žiadna žiadne nemám nejsem nejsme žádný žádná žádné
    не ни нет никогда нельзя
    nigdy żaden
    nunca jamás tampoco ningún ninguna não nem jamais nenhum nenhuma
    nicht kein keine keinen keiner nie niemals
    non mai nessuno nessuna"""
)
# "don't" is split into "don" + "t" by normalisation.
_NEG_STEMS = frozenset("don doesn didn isn aren wasn weren won wouldn shouldn couldn haven hasn hadn can".split())
# A sentence with one of these is about the person...
FIRST_PERSON = _wordset(
    """i im ive id ill me myself we
    ja som sme mňa mne jsem jsme mě mně
    я мы меня мне
    jestem
    yo soy estoy nosotros eu sou estou nós
    ich bin wir io sono"""
)
# ...and one with only these is about someone else.
THIRD_PERSON = _wordset(
    """he she they him her his hers them their theirs friend friends wife husband partner boyfriend girlfriend
    mom mum mother dad father brother sister son daughter kid kids child children colleague colleagues boss
    coworker neighbour neighbor people someone somebody everyone
    ona oni jeho jej ich kamarát kamarátka manžel manželka priateľ priateľka mama otec brat sestra kolega
    kamarád kamarádka bratr táta
    он она они его её их друг подруга жена муж брат сестра мама папа коллега
    ella ellos ellas amigo amiga esposa esposo hermano hermana madre padre ele ela eles elas marido irmão irmã mãe pai
    sie freund freundin frau mann bruder schwester mutter vater lui lei loro amica moglie fratello sorella"""
)
NEG_WINDOW = 4


def _words(text: str) -> list[str]:
    return similarity.normalize(text).split()


def _content(words: Iterable[str]) -> list[str]:
    """Stemmed content words, keeping negations and numbers."""
    out = []
    for w in words:
        if w in NEGATORS:
            out.append(w)
        elif w not in similarity.STOP and (len(w) > 1 or w.isdigit()):
            out.append(similarity.stem(w))
    return out


def clean_phrases(items) -> list[str]:
    """Normalise a list of phrases from an agent, a model or the person."""
    if isinstance(items, str):
        items = [p for p in re.split(r"[,;\n]", items)]
    out: list[str] = []
    for p in items or []:
        p = " ".join(str(p).split())[:MAX_PHRASE_CHARS].strip()
        if p and _content(_words(p)) and p.lower() not in (x.lower() for x in out):
            out.append(p)
        if len(out) >= MAX_PHRASES:
            break
    return out


def make_rule(for_phrases=None, against_phrases=None, first_person=True, source="auto") -> dict:
    return {
        "for": clean_phrases(for_phrases),
        "against": clean_phrases(against_phrases),
        "first_person": bool(first_person),
        "source": source,
    }


# Words that describe the assumption's framing, not its content ("prefers to X" -> "X").
_FRAMING = frozenset(
    """prefers prefer likes like loves love hates hate enjoys enjoy wants want plans plan tends tend
    usually often always generally seems seem probably person someone user""".split()
)
_CLAUSE = re.compile(r"[,;:]|\band\b|\bbut\b|\bor\b", re.I)


def auto_rule(guess_text: str) -> dict:
    """A rule built from the assumption's own words: one phrase per clause, framing words dropped.
    Evidence against comes from negation of those phrases."""
    phrases = []
    for clause in [guess_text] + _CLAUSE.split(guess_text or ""):
        words = [w for w in _words(clause) if w not in NEGATORS and w not in _FRAMING]
        content = [w for w in words if w not in similarity.STOP and (len(w) > 1 or w.isdigit())]
        if content:
            phrases.append(" ".join(content))
    return make_rule(phrases, [], True, "auto")


def is_valid(rule) -> bool:
    return isinstance(rule, dict) and bool(rule.get("for") or rule.get("against"))


def loads(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        r = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return r if is_valid(r) else None


def _find(phrase_tokens: list[str], sent: list[str]) -> int:
    """Index where the phrase starts in the sentence's content tokens, or -1."""
    n = len(phrase_tokens)
    if n == 0:
        return -1
    if n > 3:
        have = set(sent)
        hits = sum(1 for t in set(phrase_tokens) if t in have)
        if hits / len(set(phrase_tokens)) >= 0.75:
            for i, t in enumerate(sent):
                if t in phrase_tokens:
                    return i
        return -1
    for start, tok in enumerate(sent):
        if tok != phrase_tokens[0]:
            continue
        pos, ok = start, True
        for want in phrase_tokens[1:]:
            nxt = next((j for j in range(pos + 1, min(len(sent), pos + 4)) if sent[j] == want), None)
            if nxt is None:
                ok = False
                break
            pos = nxt
        if ok:
            return start
    return -1


def _negated(raw_words: list[str], content: list[str], idx: int) -> bool:
    """Is there a negation just before content token `idx`?"""
    before = content[max(0, idx - NEG_WINDOW) : idx]
    if any(t in NEGATORS for t in before):
        return True
    # "don't eat meat": normalisation gives "don t eat meat"; check the raw words too.
    target = content[idx]
    for i, w in enumerate(raw_words):
        if similarity.stem(w) == target:
            window = raw_words[max(0, i - NEG_WINDOW - 1) : i]
            if any(x in NEGATORS for x in window):
                return True
            for a, b in zip(window, window[1:]):
                if a in _NEG_STEMS and b == "t":
                    return True
            break
    return False


def about_person(raw_words: list[str]) -> bool:
    """True unless the sentence is clearly about someone else."""
    ws = set(raw_words)
    if ws & FIRST_PERSON:
        return True
    return not (ws & THIRD_PERSON)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s and s.strip()]


def check(rule: dict, text: str) -> Optional[dict]:
    """Evidence in `text` for one rule: {'direction': 'for'|'against', 'phrase', 'excerpt'} or None.

    At most one result per message. If a message holds both, contrary evidence wins
    (the conservative choice: it never brings a notice closer).
    """
    if not is_valid(rule) or not text:
        return None
    found_for = found_against = None
    for sent in sentences(text):
        raw = _words(sent)
        if not raw:
            continue
        if rule.get("first_person", True) and not about_person(raw):
            continue
        content = _content(raw)
        for phrase in rule.get("for") or []:
            idx = _find(_content(_words(phrase)), content)
            if idx < 0:
                continue
            if _negated(raw, content, idx):
                found_against = found_against or (phrase, sent)
            else:
                found_for = found_for or (phrase, sent)
        for phrase in rule.get("against") or []:
            idx = _find(_content(_words(phrase)), content)
            if idx >= 0 and not _negated(raw, content, idx):
                found_against = found_against or (phrase, sent)
    if found_against:
        return {"direction": "against", "phrase": found_against[0], "excerpt": found_against[1][:MAX_EXCERPT]}
    if found_for:
        return {"direction": "for", "phrase": found_for[0], "excerpt": found_for[1][:MAX_EXCERPT]}
    return None


LLM_PROMPT = """You write an evidence rule for an assumption about a person that the person rejected.
The rule is checked later by simple word matching against the person's own messages.

Assumption: "{guess}"

Return only JSON: {{"for": [...], "against": [...]}}
- "for": up to 8 short phrases (1 to 3 words) that, if the person said them about
  themselves, would support the assumption.
- "against": up to 8 short phrases that would contradict it.
- Include common word forms. If the person may write in {languages}, include phrases in those languages too.
- No full sentences, no explanations."""


def parse_llm_rule(text: str) -> Optional[dict]:
    """Pull a rule out of a model reply. Returns None if it isn't usable."""
    if not text:
        return None
    m = re.search(r"\{.*\}", str(text), re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    rule = make_rule(data.get("for"), data.get("against"), True, "llm")
    return rule if is_valid(rule) else None
