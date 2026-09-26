"""Tool and slash-command handlers. Tool handlers always return JSON strings and never raise."""
from __future__ import annotations

import json
import os

from . import inject, ledger, screen

ACTIONS = ["compile", "stage", "reject", "reconsent", "forget", "review", "history", "screen", "corroborate", "counter"]

SCHEMA = {
    "name": "shl",
    "description": (
        "Shadow-Hypothesis Ledger. Only ACTIVE guesses about the person are prompt fuel. "
        "When the person says a guess about them is wrong, call reject. Never stage a rejected guess again "
        "under a new id; only the person can restore it (reconsent REVIEW). Use screen on any text you are "
        "about to save to memory or a summary. Scores are labels and never promote a guess."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ACTIONS},
            "hypothesis_id": {"type": "string", "description": "Required except for compile, review, history and screen."},
            "text": {"type": "string", "description": "Guess text for stage, or text to check for screen."},
            "why": {"type": "string", "description": "Reason for reject, in the person's words if possible."},
            "confidence": {"type": "number", "description": "Optional label 0-1 for stage. Stored, never used as a weight."},
            "reconsent_action": {"type": "string", "enum": ["REVIEW", "DEFER", "DELETE"]},
        },
        "required": ["action"],
    },
}


def handle(args: dict, **kwargs) -> str:
    del kwargs
    try:
        os.environ["SHL_QUIET"] = "1"
        action = str(args.get("action") or "").strip().lower()
        hid = args.get("hypothesis_id")
        needs_id = {"stage", "reject", "reconsent", "forget", "corroborate", "counter"}
        if action in needs_id and not hid:
            return json.dumps({"error": f"{action} needs hypothesis_id"})
        if action == "compile":
            ctx = ledger.compile_active_context()
            ctx["active"] = [{"id": a, "text": t} for a, t in ctx["active"]]
            ctx["quarantined"] = [{"id": a, "status": s} for a, s in ctx["quarantined"]]
            return json.dumps(ctx)
        if action == "stage":
            if not args.get("text"):
                return json.dumps({"error": "stage needs text"})
            return json.dumps(ledger.stage(hid, args["text"], args.get("confidence", 0.7)))
        if action == "reject":
            return json.dumps(ledger.reject(hid, args.get("why") or "rejected by the person"))
        if action == "reconsent":
            return json.dumps(ledger.reconsent(hid, args.get("reconsent_action") or ""))
        if action == "forget":
            return json.dumps(ledger.forget(hid))
        if action == "review":
            return json.dumps({"due": ledger.review_due()})
        if action == "history":
            return json.dumps({"events": ledger.history(hid)})
        if action == "screen":
            return json.dumps(screen.screen(args.get("text") or ""))
        if action == "corroborate":
            return json.dumps(ledger.corroborate(hid))
        if action == "counter":
            return json.dumps(ledger.counter(hid))
        return json.dumps({"error": "unknown action", "actions": ACTIONS})
    except Exception as e:  # never raise into the agent loop
        return json.dumps({"error": str(e)})


USAGE = (
    "usage: /shl compile | stage ID text | reject ID why | reconsent ID REVIEW|DEFER|DELETE | "
    "forget ID | review | history [ID] | inject"
)


def slash(raw_args: str) -> str:
    os.environ["SHL_QUIET"] = "1"
    raw = (raw_args or "").strip()
    if not raw or raw.split()[0].lower() in {"compile", "status"}:
        ctx = ledger.compile_active_context()
        lines = [f"SHL  ACTIVE={ctx['active_count']}  PARKED={ctx['quarantined_count']}", "ACTIVE:"]
        lines += [f"  {hid}: {text[:200]}" for hid, text in ctx["active"]]
        lines.append("PARKED ids: " + (", ".join(h for h, _ in ctx["quarantined"]) or "(none)"))
        return "\n".join(lines)
    parts = raw.split(None, 2)
    cmd = parts[0].lower()
    hid = parts[1] if len(parts) > 1 else ""
    rest = parts[2] if len(parts) > 2 else ""
    if cmd == "reject" and hid:
        return json.dumps(ledger.reject(hid, rest or "rejected by the person"))
    if cmd == "stage" and hid and rest:
        return json.dumps(ledger.stage(hid, rest, 0.7))
    if cmd == "reconsent" and hid:
        return json.dumps(ledger.reconsent(hid, rest.split()[0] if rest else ""))
    if cmd == "forget" and hid:
        return json.dumps(ledger.forget(hid))
    if cmd == "review":
        return json.dumps({"due": ledger.review_due()}, indent=2)
    if cmd == "history":
        return json.dumps({"events": ledger.history(hid or None)}, indent=2, default=str)
    if cmd == "corroborate" and hid:
        return json.dumps(ledger.corroborate(hid))
    if cmd == "counter" and hid:
        return json.dumps(ledger.counter(hid))
    if cmd == "inject":
        return inject.build_context("")
    return USAGE
