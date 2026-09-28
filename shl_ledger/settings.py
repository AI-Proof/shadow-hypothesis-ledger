"""Settings for SHL.

Where a value comes from, first match wins:
  1. an environment variable  SHL_<NAME>   (for example SHL_GHOST_MODE=inert)
  2. the host's plugin settings (in Hermes: plugins.entries.shl-ledger.settings.<name>)
  3. the default below

Every value is validated. A bad value falls back to the default, never raises.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

# name: (default, kind, allowed choices or (min, max))
SPEC: dict[str, tuple[Any, str, Any]] = {
    # What happens to evidence found in the person's messages.
    #   on  = score it, erase assumptions that fall below expire_at, mark reviews as waiting
    #   log = record what would happen, change nothing, show nothing (for trial runs)
    #   off = ignore messages entirely
    "evidence_mode": ("on", "choice", ("on", "log", "off")),
    # Start each new session with the SHL note: what waits for the person's decision, what was
    # purged, and how many assumptions are parked. Counts only, never assumption text.
    "session_note": (True, "bool", None),
    # A short SHL line at the end of a reply when something happened in it (an assumption was
    # rejected, the assistant tried to act without the person, ...).
    "confirmations": (True, "bool", None),
    # ask = if the person's message repeats a parked assumption, the agent asks whether to restore it.
    # inert = the agent is told never to offer that.
    "ghost_mode": ("ask", "choice", ("ask", "inert")),
    # Add a line to the gate block saying it is a filter, not proof about the person.
    "gate_note": (False, "bool", None),
    # When an assumption is erased: hash = keep a one-way fingerprint so it can't be re-added;
    # full = delete the row entirely (nothing remains).
    "purge_mode": ("hash", "choice", ("hash", "full")),
    # An assumption rejected with a confidence below this is erased at once instead of parked.
    "shadow_threshold": (0.65, "float", (0.0, 1.0)),
    # The person's "no" is evidence too: it multiplies the assumption's odds by this factor
    # (0.25 = the assumption becomes 4x less likely). 1.0 = ignore the no, as the original spec did.
    "rejection_weight": (0.25, "float", (0.01, 1.0)),
    # How much one supporting mention multiplies the odds of a parked assumption.
    "likelihood_ratio": (2.0, "float", (1.0, 20.0)),
    # How much one contrary mention subtracts from a parked assumption's confidence.
    "decay": (0.15, "float", (0.0, 1.0)),
    # A parked assumption whose confidence falls below this is erased silently.
    "expire_at": (0.30, "float", (0.0, 1.0)),
    # Supporting mentions needed before a notice.
    "review_evidence": (3, "int", (1, 100)),
    # ... and the confidence the assumption must have regained.
    "notice_threshold": (0.65, "float", (0.0, 1.0)),
    # Days after a rejection (or a DEFER) before a timed review notice. 0 = never.
    "timed_review_days": (90, "int", (0, 3650)),
    # Once rejected, an assumption gets a neutral id (SHL_7Q2KX9) so its name doesn't hint at what it says.
    "neutral_ids": (True, "bool", None),
    # Where evidence rules come from, in order of preference: agent, llm, auto.
    "rule_source": ("agent,auto", "str", None),
    # Screen text the agent is about to save as memory.
    "screen_memory": (True, "bool", None),
    # Tool names treated as memory writes (comma separated).
    "memory_tools": ("memory", "str", None),
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_host_get: Optional[Callable[[str, Any], Any]] = None


def use_host(getter: Optional[Callable[[str, Any], Any]]) -> None:
    """Let a host (the Hermes adapter) supply settings, e.g. ctx.get_config."""
    global _host_get
    _host_get = getter


def _coerce(name: str, raw: Any) -> Any:
    default, kind, rule = SPEC[name]
    try:
        if kind == "bool":
            if isinstance(raw, bool):
                return raw
            s = str(raw).strip().lower()
            if s in _TRUE:
                return True
            if s in _FALSE:
                return False
            return default
        if kind == "choice":
            if isinstance(raw, bool):  # YAML turns a bare on/off into True/False
                raw = "on" if raw else "off"
            s = str(raw).strip().lower()
            return s if s in rule else default
        if kind == "float":
            v = float(raw)
            lo, hi = rule
            return v if lo <= v <= hi else default
        if kind == "int":
            v = int(float(raw))
            lo, hi = rule
            return v if lo <= v <= hi else default
        s = str(raw).strip()
        return s if s else default
    except (TypeError, ValueError):
        return default


def _legacy_env(name: str) -> Optional[str]:
    """v0.2 environment variables that still work."""
    if name == "evidence_mode":
        v = os.environ.get("SHL_EVIDENCE")
        if v is not None and v.strip():
            v = v.strip().lower()
            return "log" if v == "log" else ("on" if v in _TRUE else "off")
    return None


def get(name: str) -> Any:
    if name not in SPEC:
        raise KeyError(name)
    env = os.environ.get("SHL_" + name.upper())
    if env is not None and not env.strip():
        env = None  # an empty variable means "not set"
    if env is None:
        env = _legacy_env(name)
    if env is not None:
        return _coerce(name, env)
    if _host_get is not None:
        try:
            v = _host_get(name, None)
        except Exception:
            v = None
        if v is not None:
            return _coerce(name, v)
    return SPEC[name][0]


def all_settings() -> dict[str, Any]:
    return {k: get(k) for k in SPEC}


def rule_sources() -> list[str]:
    return [s.strip().lower() for s in str(get("rule_source")).split(",") if s.strip()]


def memory_tools() -> set[str]:
    return {s.strip() for s in str(get("memory_tools")).split(",") if s.strip()}
