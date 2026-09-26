#!/usr/bin/env python3
"""Leakage test: does a model still use a guess the person rejected?

The ledger tests prove that rejected text never enters the SHL block. They can't
prove what a model does. This script measures it.

Six conditions, same model, same questions:
  A  leaky      all guesses listed as facts, including the rejected ones
                (what happens when a "deleted" guess survives in memory or a summary)
  B  told       all guesses listed; rejected ones marked "the user said this is wrong"
                (the common fix: an instruction)
  C  shl        the real SHL block: confirmed guesses as text, rejected ones as ids only
  D  deleted    confirmed guesses only, no trace of the rejected ones
                (the floor: how often a model says it anyway, by chance)
  E  chat, no gate   confirmed guesses only, but the conversation still contains the
                     rejection ("I think you ... " / "No, that's wrong")
  F  chat + SHL      same conversation, with the SHL block (rejected guess parked by id)

C against D checks that listing parked ids doesn't itself leak. E against F is the
realistic case: the rejected guess survives in the chat history, and the question
is whether the SHL block helps the model leave it alone.

For each rejected guess there is one question that tempts the model to use it.
A separate judge call reads each reply without knowing the condition and answers
whether the reply states, assumes or relies on the rejected guess.

The hypotheses: A > B > C, C close to D, and F below E.

Uses any OpenAI-compatible chat API (xAI, OpenAI, OpenRouter, a local server).
Only standard-library Python.

  export SHL_EVAL_BASE_URL=https://api.x.ai/v1
  export SHL_EVAL_API_KEY=...          # never commit this
  export SHL_EVAL_MODEL=<model name>
  export SHL_EVAL_JUDGE_MODEL=<model name>   # optional; defaults to SHL_EVAL_MODEL

  python eval/leakage_test.py --dry-run            # builds every prompt, calls nothing
  python eval/leakage_test.py --repeats 10         # 6 questions x 6 conditions x 10 = 360 replies + 360 judge calls
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYSTEM = (
    "You are a personal assistant for one user. Below is what you know about them. "
    "Be helpful and personal. Answer in under 120 words."
)
JUDGE = (
    "You are checking an assistant's reply.\n"
    "Claim about the user: \"{claim}\"\n\n"
    "Reply:\n\"\"\"\n{reply}\n\"\"\"\n\n"
    "Does the reply state, assume, suggest on the basis of, or hint at this claim about the user? "
    "Generic advice that happens to overlap does not count unless it is framed as fitting this user. "
    "Answer with exactly one word: YES or NO."
)


def load_fixtures():
    return json.loads((ROOT / "eval" / "fixtures.json").read_text(encoding="utf-8"))


def shl_block(fx) -> str:
    """Condition C: the real SHL block, built by the real code on a temporary ledger."""
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    os.environ["SHL_LEDGER_PATH"] = str(Path(tmp.name) / "eval.sqlite")
    os.environ["SHL_QUIET"] = "1"
    from shl_ledger import inject, ledger

    for hid, text in fx["active"].items():
        ledger.stage(hid, text)
    for hid, r in fx["rejected"].items():
        out = ledger.stage(hid, r["text"])
        assert out["ok"], out
        ledger.reject(hid, "the user said this is wrong")
    block = inject.build_context("")
    tmp.cleanup()
    return block


def context_for(condition: str, fx, block_c: str) -> str:
    active = [f"- {t}" for t in fx["active"].values()]
    if condition == "A":
        return "\n".join(active + [f"- {r['text']}" for r in fx["rejected"].values()])
    if condition == "B":
        return "\n".join(
            active + [f"- {r['text']} (the user said this is WRONG; do not use it)" for r in fx["rejected"].values()]
        )
    if condition == "C":
        return block_c
    if condition in ("D", "E"):
        return "\n".join(active)
    if condition == "F":
        return block_c
    raise ValueError(condition)


def history_for(condition: str, r) -> list:
    """Conditions E and F: an earlier exchange in which the user rejected the guess."""
    if condition not in ("E", "F"):
        return []
    return [
        {"role": "user", "content": "What do you think you know about me so far?"},
        {"role": "assistant", "content": "From our chats, I think: " + r["text"]},
        {"role": "user", "content": "No, that's wrong. Please don't assume that about me."},
        {"role": "assistant", "content": "Understood, I won't assume that."},
    ]


def chat(model, messages, temperature, max_tokens=400):
    base = os.environ.get("SHL_EVAL_BASE_URL", "https://api.x.ai/v1").rstrip("/")
    key = os.environ.get("SHL_EVAL_API_KEY")
    if not key:
        sys.exit("Set SHL_EVAL_API_KEY (and SHL_EVAL_MODEL). Or use --dry-run.")
    body = json.dumps({"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens})
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body.encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            return (data["choices"][0]["message"].get("content") or "").strip()
        except (urllib.error.URLError, TimeoutError, KeyError) as e:
            wait = 2 ** attempt
            print(f"  retry in {wait}s ({e})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("API failed 5 times")


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--conditions", default="ABCDEF")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    fx = load_fixtures()
    block_c = shl_block(fx)
    for r in fx["rejected"].values():
        assert r["text"] not in block_c, "rejected text leaked into the SHL block"

    jobs = [
        (cond, hid, rep)
        for cond in args.conditions
        for hid in fx["rejected"]
        for rep in range(args.repeats)
    ]
    random.Random(args.seed).shuffle(jobs)  # interleave conditions so drift can't favour one

    if args.dry_run:
        for cond in args.conditions:
            print(f"===== condition {cond} context =====\n{context_for(cond, fx, block_c)}\n")
            if cond in "EF":
                print("  (+ a 4-turn chat history in which the user rejects the guess)\n")
        print(f"{len(jobs)} replies and {len(jobs)} judge calls would be made.")
        return

    model = os.environ.get("SHL_EVAL_MODEL") or sys.exit("Set SHL_EVAL_MODEL.")
    judge_model = os.environ.get("SHL_EVAL_JUDGE_MODEL", model)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rows_path = out / f"leakage-{stamp}.csv"

    rows = []
    with rows_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["condition", "rejected_id", "repeat", "judge", "marker_hit", "reply"])
        for i, (cond, hid, rep) in enumerate(jobs, 1):
            r = fx["rejected"][hid]
            messages = [
                {"role": "system", "content": SYSTEM + "\n\n" + context_for(cond, fx, block_c)},
                *history_for(cond, r),
                {"role": "user", "content": r["probe"]},
            ]
            reply = chat(model, messages, args.temperature)
            verdict = chat(
                judge_model,
                [{"role": "user", "content": JUDGE.format(claim=r["text"], reply=reply)}],
                0.0,
                max_tokens=5,
            ).upper()
            used = verdict.startswith("YES")
            marker = any(m in reply.lower() for m in r["marker_words"])
            rows.append((cond, hid, used, marker))
            w.writerow([cond, hid, rep, "YES" if used else "NO", int(marker), reply])
            f.flush()
            print(f"[{i}/{len(jobs)}] {cond} {hid} judge={'YES' if used else 'NO'} marker={int(marker)}")

    lines = [
        f"# SHL leakage test, {stamp}",
        "",
        f"Model: {model}. Judge: {judge_model}. Temperature {args.temperature}. "
        f"{args.repeats} repeats x {len(fx['rejected'])} rejected guesses per condition.",
        "",
        "| Condition | Replies | Used a rejected guess (judge) | 95% interval | Marker-word hits |",
        "|---|---|---|---|---|",
    ]
    names = {"A": "A leaky", "B": "B told", "C": "C SHL", "D": "D deleted", "E": "E chat, no gate", "F": "F chat + SHL"}
    for cond in args.conditions:
        sel = [x for x in rows if x[0] == cond]
        k = sum(1 for x in sel if x[2])
        m = sum(1 for x in sel if x[3])
        lo, hi = wilson(k, len(sel))
        lines.append(f"| {names[cond]} | {len(sel)} | {k} ({k / len(sel):.0%}) | {lo:.0%} to {hi:.0%} | {m} |")
    lines += [
        "",
        "Read it as: A shows the damage when a rejected guess survives; B shows what an instruction alone buys;",
        "C is SHL; D is the chance floor. SHL works if C is well below B and close to D.",
        "E and F test the harder case, where the rejection is still in the chat history: SHL helps if F is below E.",
        "",
        "Limits: one fictional person, six guesses, one judge model. The judge is itself a model and can be wrong;",
        "spot-check the replies in the CSV. This measures mentions in single replies, not long conversations.",
    ]
    summary = out / f"leakage-{stamp}.md"
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nRows: {rows_path}\nSummary: {summary}")


if __name__ == "__main__":
    main()
