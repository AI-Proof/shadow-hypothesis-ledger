"""The agent's `shl` tool, the person's `/shl` command, and text reports.

The agent's tool never returns the text of a parked (rejected) assumption or its
evidence rule: tool results go into the model's context. The `/shl` command is the
person's channel (in Hermes its output never enters the model's history), so it may
show parked text, and it is where consent codes are shown.

Everyday commands take the short, stable number of a parked assumption (#55):
    /shl            what's waiting for you
    /shl 55         the story of #55
    /shl use 55     use it again          (older word: reconsent 55 REVIEW)
    /shl later 55   keep it parked        (DEFER)
    /shl drop 55    erase it for good     (DELETE)
    /shl all        every parked assumption
    /shl help [all]
"""
from __future__ import annotations

import json
import re
import time

from . import hooks, inject, ledger, notices, rules, screen, settings

__all__ = ["SCHEMA", "handle", "slash", "USAGE", "report_text"]

ACTIONS = ["compile", "stage", "reject", "reconsent", "forget", "review", "notices", "history", "screen"]

SCHEMA = {
    "name": "shl",
    "description": (
        "Shadow-Hypothesis Ledger: assumptions about the person. Only ACTIVE assumptions may be used. "
        "stage = record a new assumption (use neutral ids: HYP_014, not HYP_VEGETARIAN). When the person says an "
        "assumption about them is wrong, call reject with their reason and short phrases: evidence_for (words that "
        "would support it if they said them later) and evidence_against (words that would contradict it). "
        "A rejected assumption gets a neutral id and a number (#55); never stage it again under a new id. Only the "
        "person decides about a rejected assumption: reconsent works only if they typed a line like 'use 55 K7Q2' "
        "with the code shown to them by /shl 55; otherwise tell them to run /shl use 55 (or later / drop). "
        "Use screen on text you are about to save outside memory. Scores are labels and never promote anything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ACTIONS},
            "hypothesis_id": {
                "type": "string",
                "description": "Required for stage, reject, reconsent and forget. For a parked assumption, its id or number (55).",
            },
            "text": {"type": "string", "description": "Assumption text for stage, or text to check for screen."},
            "why": {"type": "string", "description": "Reason for reject, in the person's words if possible."},
            "evidence_for": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For reject: up to 8 short phrases (1-3 words) that would support the assumption if the person said them about themselves later. Include other languages the person writes in.",
            },
            "evidence_against": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For reject: up to 8 short phrases that would contradict the assumption.",
            },
            "source_note": {"type": "string", "description": "For reject: where the assumption came from (one line)."},
            "confidence": {"type": "number", "description": "Optional label 0-1 for stage."},
            "reconsent_action": {"type": "string", "enum": ["REVIEW", "DEFER", "DELETE"]},
        },
        "required": ["action"],
    },
}

_WORD = {"REVIEW": "use", "DEFER": "later", "DELETE": "drop"}


def _phrases(v):
    if v is None:
        return None
    if isinstance(v, str):
        return [p for p in re.split(r"[,;\n]", v) if p.strip()]
    if isinstance(v, (list, tuple)):
        return [str(p) for p in v]
    return None


def _label(hid) -> str:
    n = ledger.number(hid) if hid else None
    return f"#{n}" if n else str(hid)


# --------------------------------------------------------------------------- the agent's tool


def handle(args: dict, **kwargs) -> str:
    """The agent's tool. Always returns a JSON string, never raises."""
    try:
        args = args or {}
        action = str(args.get("action") or "").strip().lower()
        hid = args.get("hypothesis_id")
        sid = kwargs.get("session_id") or hooks.current_context().get("session_id") or ""
        needs_id = {"stage", "reject", "reconsent", "forget"}
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
            out = ledger.stage(hid, args["text"], args.get("confidence", ledger.DEFAULT_CONFIDENCE))
            if not out.get("ok") and out.get("matches"):
                hooks.confirm(notices.readd_blocked([_label(m) for m in out["matches"]]), sid)
            return json.dumps(out)
        if action == "reject":
            context = hooks.current_context()
            for k in ("session_id", "turn_id"):
                if kwargs.get(k):
                    context[k] = kwargs[k]
            out = ledger.reject(
                hid,
                args.get("why") or "rejected by the person",
                evidence_for=_phrases(args.get("evidence_for")),
                evidence_against=_phrases(args.get("evidence_against")),
                source_note=args.get("source_note") or "",
                context=context,
            )
            if out.get("ok") and out.get("status") == "SHADOW" and out.get("num"):
                hooks.confirm_parked(f"#{out['num']}", sid)
            elif out.get("ok") and out.get("status") in ("ERASED", "PURGED"):
                hooks.confirm(notices.erased_weak(settings.get("shadow_threshold")), sid)
            return json.dumps(out)
        if action == "reconsent":
            act = ledger.VERBS.get(str(args.get("reconsent_action") or "").upper(), "")
            out = ledger.reconsent(hid, act, by="agent", session_id=sid)
            label = f"#{out['num']}" if out.get("num") else str(hid)
            if out.get("ok"):
                hooks.confirm(notices.decided(label, act, settings.get("timed_review_days")), sid)
            elif act and "needs the person" in str(out.get("error", "")):
                hooks.confirm(notices.blocked(label, _WORD[act]), sid)
            return json.dumps(out)
        if action == "forget":
            target = ledger.resolve(hid) or str(hid)
            if ledger.status(target) in ledger.PARKED:
                label = _label(target)
                hooks.confirm(notices.blocked(label, "erase"), sid)
                return json.dumps(
                    {
                        "ok": False,
                        "id": target,
                        "error": f"a rejected assumption is erased by the person: ask them to run /shl drop "
                        f"{label.lstrip('#')}, or to type the drop line with the code shown by /shl {label.lstrip('#')}",
                    }
                )
            return json.dumps(ledger.forget(target))
        if action == "review":
            return json.dumps({"due": ledger.review_due()})
        if action == "notices":
            return json.dumps({"waiting": ledger.pending_notices()})
        if action == "history":
            # Only ids that still exist: an assumption's name from before it was rejected (and
            # renamed) may describe it, so the agent never sees events filed under old ids.
            current = ledger.current_ids()
            target = (ledger.resolve(hid) or hid) if hid else None
            if target and target not in current:
                return json.dumps({"events": []})
            events = [e for e in ledger.history(target, limit=200) if e["id"] in current][:50]
            return json.dumps({"events": events}, default=str)
        if action == "screen":
            return json.dumps(screen.screen(args.get("text") or ""))
        return json.dumps({"error": "unknown action", "actions": ACTIONS})
    except Exception as e:  # never raise into the agent loop
        return json.dumps({"error": str(e)})


# --------------------------------------------------------------------------- the person's /shl

USAGE_EVERYDAY = """EVERYDAY
/shl            what's waiting for you
/shl 55         the story of #55 (why it came back)
/shl use 55     use it again
/shl later 55   keep it parked, ask me again later
/shl drop 55    erase it for good
/shl all        every parked assumption
/shl help all   more commands"""

USAGE_MORE = """MORE
/shl active             assumptions the assistant may use
/shl add ID text        add an assumption yourself (even one that was erased)
/shl reject ID why      reject an assumption yourself
/shl forget 55          erase an assumption's text for good
/shl rule 55            how #55 is tracked; "/shl rule 55 for a, b against c" changes it
/shl check TEXT         dry run: which parked assumptions would this sentence count for?
/shl report [full]      summary of the last 14 days
/shl history [55]       change log
/shl verify             check the change log hasn't been edited
/shl settings           settings in effect
/shl rule ALL auto      evidence rules for older assumptions that have none
/shl rename ALL         neutral ids for older assumptions
Older words still work: review = /shl, reconsent 55 REVIEW|DEFER|DELETE, stage = add."""

USAGE = USAGE_EVERYDAY + "\n\n" + USAGE_MORE


def _ts(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


def _day(t):
    return time.strftime("%d %b %Y", time.localtime(t)).lstrip("0") if t else "-"


def _f(x):
    return "-" if x is None else f"{x:.2f}"


def _clip(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _main_view() -> str:
    wait = ledger.waiting(viewed=True)
    ctx = ledger.compile_active_context()
    parked, active = ctx["quarantined_count"], ctx["active_count"]
    days = settings.get("timed_review_days")
    lines = []
    if wait:
        lines.append("WAITING FOR YOU")
        for w in wait:
            start = w["conf_after_no"] if w["conf_after_no"] is not None else w["conf_at_reject"]
            why = (
                f"{_n(w['support'], 'supporting mention')}" if w["reason"] == "evidence" else f"{days}-day check"
            )
            lines.append(f" {w['num']}. \"{_clip(w['text'])}\"")
            lines.append(f"    {_f(start)} → {_f(w['confidence'])} · {why}")
        lines += [""] + _commands(wait[0]["num"]) + [_pad("/shl all", wait[0]["num"]) + f"everything parked ({parked})"]
    elif parked:
        lines.append(
            f"{_n(parked, 'assumption')} you rejected {'is' if parked == 1 else 'are'} parked in your "
            "Shadow-Hypothesis Ledger. No actions necessary."
        )
        lines += ["", "/shl all    see all SHL assumptions", "/shl help   what else you can do"]
    else:
        lines.append("No rejected assumptions are parked. When the assistant assumes something about you")
        lines.append("that's wrong, just say so: it will be parked here and never used unless you decide.")
    lines += ["", f"In use: {_n(active, 'assumption')} the assistant may use (/shl active)."]
    mode = settings.get("evidence_mode")
    if mode != "on":
        lines.append(f"Evidence mode: {mode}" + (" (trial: nothing changes)" if mode == "log" else ""))
    return notices.frame("\n".join(lines))


def _pad(cmd: str, n) -> str:
    return cmd.ljust(len(f"/shl later {n}") + 3)


def _commands(n) -> list:
    return [
        _pad(f"/shl use {n}", n) + "use it again",
        _pad(f"/shl later {n}", n) + "keep it parked, ask me again later",
        _pad(f"/shl drop {n}", n) + "erase it for good",
        _pad(f"/shl {n}", n) + "show why (the evidence)",
    ]


def _n(k, one):
    return f"{k} {one if k == 1 else one + 's'}"


def _all_view() -> str:
    items = ledger.parked_list()
    if not items:
        return notices.frame("No rejected assumptions are parked.")
    lines = [f"PARKED ({len(items)})"]
    for it in items:
        tag = " · waiting for you" if it["waiting"] else ""
        num = it["num"] if it["num"] else it["id"]
        lines.append(f" {num}. \"{_clip(it['text'])}\"  {_f(it['confidence'])}{tag}")
    n = next((it["num"] for it in items if it["num"]), "55")
    lines += ["", f"/shl {n} for the story of one; /shl use | later | drop {n} to decide."]
    return notices.frame("\n".join(lines))


def _active_view() -> str:
    ctx = ledger.compile_active_context()
    if not ctx["active"]:
        return notices.frame("No assumptions are in use.")
    lines = [f"IN USE ({ctx['active_count']})"]
    lines += [f" {hid}: {_clip(text, 120)}" for hid, text in ctx["active"]]
    lines += ["", "Wrong? Tell the assistant, or: /shl reject ID why"]
    return notices.frame("\n".join(lines))


def _detail_text(ref: str, framed: bool = True) -> str:
    d = ledger.detail(ref)
    if not d:
        out = f"{ref}: not found. /shl all lists every parked assumption."
        return notices.frame(out) if framed else out
    label = f"#{d['num']}" if d.get("num") else d["id"]
    num = str(d.get("num") or d["id"])
    lines = [f"{label}  \"{d['text']}\"" if d["text"] else f"{label}  (text erased)"]
    waiting = d["notice"] is not None
    state = {
        "ACTIVE": "in use",
        "SHADOW": "parked",
        "REINFORCED": "parked (your messages have supported it)",
        "EXPIRED": "parked (older ledger)",
        "ERASED": "erased",
    }.get(d["status"], d["status"])
    if waiting:
        state = "waiting for your decision" + (
            " (it crossed the threshold)" if d["notice"]["reason"] == "evidence" else " (periodic check)"
        )
    lines.append(f"Status: {state}")
    after_no = d["rejection"].get("conf_after_no")
    if d["conf_at_reject"] is not None or d["confidence"] is not None:
        mid = f", {_f(after_no)} after it" if isinstance(after_no, (int, float)) else ""
        lines.append(f"Confidence: {_f(d['conf_at_reject'])} before your no{mid}, {_f(d['confidence'])} now")
    facts = [f"supporting {d['support']}", f"against {d['against']}"]
    if d["rejected_ts"]:
        facts.append(f"rejected {_day(d['rejected_ts'])}")
    if d["next_review_ts"]:
        facts.append(f"next check {_day(d['next_review_ts'])}")
    lines.append(" · ".join(facts))
    if d["rejection"].get("why"):
        lines.append(f"Your reason: {d['rejection']['why']}")
    if d["evidence"]:
        lines.append("Evidence (your own words):")
        for e in d["evidence"][-8:]:
            tag = "" if e["applied"] else "  (trial only)"
            lines.append(
                f"  {_day(e['ts'])}  {e['direction']:<7} {_f(e['conf_before'])} → {_f(e['conf_after'])}  "
                f"\"{_clip(e['excerpt'], 70)}\"{tag}"
            )
    if d["rule"]:
        r = d["rule"]
        lines.append(
            f"Tracked by: for {', '.join(r.get('for') or []) or '-'} · against {', '.join(r.get('against') or []) or '-'}"
        )
    if d["status"] in ledger.PARKED:
        lines += [""] + _commands(num)[:3]
        if waiting and d["notice"].get("code"):
            lines.append(
                f"Or tell the assistant: use {num} {d['notice']['code']}  (or later / drop; valid 7 days)"
            )
    return notices.frame("\n".join(lines)) if framed else "\n".join(lines)


def _parse_rule_args(rest: str):
    """'for a, b against c, d' -> (['a','b'], ['c','d'])."""
    m = re.match(r"(?is)^\s*(?:\bfor\b:?\s*(?P<f>.*?))?\s*(?:\bagainst\b:?\s*(?P<a>.*))?$", rest or "")
    if not m:
        return None, None
    return _phrases(m.group("f") or ""), _phrases(m.group("a") or "")


def _check_text(text: str) -> str:
    out = []
    with ledger.conn(write=False) as c:
        rows = list(
            c.execute(
                "SELECT hypothesis_id, num, rule_json FROM shl WHERE status IN ('SHADOW','REINFORCED') AND rule_json IS NOT NULL"
            )
        )
    for hid, num, raw in rows:
        r = rules.loads(raw)
        hit = rules.check(r, text) if r else None
        if hit:
            out.append(f"  #{num or hid}: {hit['direction']}  (phrase \"{hit['phrase']}\")")
    return "Dry run, nothing recorded:\n" + (
        "\n".join(out) if out else "  no parked assumption would count this message"
    )


def _rule_text(ref: str, rest: str) -> str:
    if not rest:
        r = ledger.get_rule(ref)
        if not r:
            return f"{ref}: no rule (is it parked?)"
        return f"#{ref.lstrip('#')} is tracked by:\n  for: {', '.join(r.get('for') or []) or '-'}\n  against: {', '.join(r.get('against') or []) or '-'}"
    if rest.strip().lower() == "auto":
        out = ledger.set_rule(ref, auto=True)
    else:
        f, a = _parse_rule_args(rest)
        out = ledger.set_rule(ref, f, a)
    if not out.get("ok"):
        return out.get("error", "could not change the rule")
    r = out["rule"]
    return f"Updated. Now tracked by:\n  for: {', '.join(r['for']) or '-'}\n  against: {', '.join(r['against']) or '-'}"


def report_text(days: int = 14, full: bool = False) -> str:
    """A markdown summary. Ids, numbers and counts only unless full=True."""
    s = ledger.stats(days)
    st = settings.all_settings()
    v = ledger.verify_log()
    lines = [
        f"# SHL report, last {days} days",
        "",
        f"Database: `{ledger.db_path()}`",
        f"Mode: evidence `{st['evidence_mode']}`, session note `{'on' if st['session_note'] else 'off'}`, "
        f"ghost `{st['ghost_mode']}`, purge `{st['purge_mode']}`, timed review {st['timed_review_days']} days",
        f"Change log: {'intact' if v['ok'] else 'BROKEN at entry ' + str(v.get('broken_at'))}, {v['entries']} entries",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for k in ledger.STATUSES:
        if s["by_status"].get(k):
            lines.append(f"| {k} | {s['by_status'][k]} |")
    lines += [
        "",
        f"Became due for review: {s['notices_shown']}. Timed reviews due now: {s['timed_reviews_due']}.",
    ]
    if s["erased"]:
        lines.append("Erased (decayed, or rejected below the threshold): " + ", ".join(s["erased"]))
    lines += ["", "## Evidence found in your messages", ""]
    if not s["hits"]:
        lines.append("None.")
    else:
        lines += ["| Assumption | Direction | Applied | Would | Count |", "|---|---|---|---|---|"]
        for h in s["hits"]:
            would = h["note"].replace("would_", "") if h["note"] else ""
            lines.append(
                f"| {_label(h['id'])} | {h['direction']} | {'yes' if h['applied'] else 'trial only'} | {would} | {h['count']} |"
            )
    pend = ledger.pending_notices()
    if pend:
        lines += ["", "## Waiting for your decision", ""]
        lines += [f"- #{p['num']} ({p['reason']})" for p in pend]
    if full:
        lines += ["", "## Details (contains assumption text; keep private)", ""]
        for it in ledger.parked_list():
            lines += ["```", _detail_text(it["id"], framed=False), "```"]
    return "\n".join(lines)


def _decide(ref: str, word: str) -> str:
    out = ledger.reconsent(ref, word, by="person")
    if not out.get("ok"):
        return notices.frame(f"{ref}: {out.get('error', 'not found')}. /shl all lists every parked assumption.")
    return notices.frame(notices.decided(out.get("label") or ref, out["action"], settings.get("timed_review_days")))


def slash(raw_args: str) -> str:
    """The person's /shl command. Output goes to the person only, framed."""
    try:
        raw = (raw_args or "").strip()
        parts = raw.split(None, 2)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""
        if cmd in {"", "status", "compile"} or (cmd == "review" and not arg):
            return _main_view()
        if re.fullmatch(r"#?\d+", cmd):
            return _detail_text(cmd)
        if cmd == "review" and arg:
            return _detail_text(arg)
        if cmd in {"use", "later", "drop"}:
            if not arg:
                return notices.frame(f"Which one? For example: /shl {cmd} 55   (/shl shows the numbers)")
            return _decide(arg, cmd)
        if cmd == "reconsent" and arg:
            return _decide(arg, rest.split()[0] if rest else "")
        if cmd == "all":
            return _all_view()
        if cmd == "active":
            return _active_view()
        if cmd in {"help", "-h", "--help"}:
            return notices.frame(USAGE if arg.lower() == "all" else USAGE_EVERYDAY)
        if cmd in {"add", "stage"} and arg and rest:
            out = ledger.stage(arg, rest, by="person")
            msg = f"Added {arg}. The assistant may use it." if out.get("ok") else f"Not added: {out.get('error')}"
            return notices.frame(msg)
        if cmd == "reject" and arg:
            out = ledger.reject(arg, rest or "rejected by the person")
            if out.get("ok") and out.get("num"):
                return notices.frame(notices.parked([f"#{out['num']}"]))
            return notices.frame(out.get("note") or out.get("error") or json.dumps(out))
        if cmd == "forget" and arg:
            out = ledger.forget(arg)
            return notices.frame(f"Erased {arg} for good." if out.get("ok") else f"{arg}: {out.get('error')}")
        if cmd == "rule" and arg.upper() == "ALL" and rest.strip().lower() == "auto":
            out = ledger.auto_rules_for_missing()
            return notices.frame(f"Added evidence rules to {_n(out['rules_added'], 'assumption')}.")
        if cmd == "rename" and arg.upper() == "ALL":
            out = ledger.neutralize_ids()
            return notices.frame(
                f"Gave {_n(out['renamed'], 'assumption')} a neutral id. Assumptions waiting for your decision keep theirs until you answer."
            )
        if cmd == "rule" and arg:
            return notices.frame(_rule_text(arg, rest))
        if cmd == "check":
            return notices.frame(_check_text(raw[len(parts[0]) :].strip()))
        if cmd == "corroborate" and arg:
            return notices.frame(json.dumps(ledger.corroborate(ledger.resolve(arg) or arg)))
        if cmd == "counter" and arg:
            return notices.frame(json.dumps(ledger.counter(ledger.resolve(arg) or arg)))
        if cmd == "history":
            ev = ledger.history((ledger.resolve(arg) or arg) if arg else None)
            body = "\n".join(
                f"  {_ts(e['ts'])}  {_label(e['id']):<14} {e['action']:<18} {e['from'] or '-'} → {e['to'] or '-'}"
                + (f"  ({e['from_conf']} → {e['to_conf']})" if e["to_conf"] is not None or e["from_conf"] is not None else "")
                for e in ev
            )
            return notices.frame(body or "No history.")
        if cmd == "report":
            return notices.frame(report_text(full=arg.lower() == "full"))
        if cmd == "verify":
            v = ledger.verify_log()
            return notices.frame(
                f"Change log intact ({v['entries']} entries)."
                if v["ok"]
                else f"Change log edited outside SHL at entry {v.get('broken_at')}. Entries before it are intact."
            )
        if cmd == "settings":
            return notices.frame("\n".join(f"{k} = {v}" for k, v in settings.all_settings().items()))
        if cmd == "inject":
            return inject.build_context("")
        return notices.frame(f"Unknown: {raw}\n\n" + USAGE_EVERYDAY)
    except Exception as e:
        return notices.frame(f"SHL error: {e}")
