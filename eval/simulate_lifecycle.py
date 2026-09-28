#!/usr/bin/env python3
"""Lifecycle simulation: does SHL bring back the right rejected assumptions, and leave the rest alone?

No model is called. A fictional person, Alex, rejected seven assumptions. Four rejections
were correct (the assumption is false). Three were wrong, or Alex changed since (the assumption
is true). Alex then talks to the agent for 60 sessions. Each session holds a few
messages drawn at random from:

  * true assumptions:   first-person support ("I'm putting money aside for a car")
  * false assumptions:  contrary statements ("I grilled chicken tonight"),
                    traps about other people ("My sister is vegetarian"),
                    negated mentions ("I'm not vegetarian, but ..."),
                    and misleading first-person mentions that the word matcher
                    WILL count as support ("I tried a vegetarian place and liked it")
  * neutral chatter

Every message goes through the real code path (hooks.before_model), on a temporary
ledger, with a simulated clock (one session every 2 days). Timed reviews are off, so
only evidence can trigger a notice.

Measured, over many random seeds:
  false resurfacing   a correctly rejected assumption came back for review (the number that matters most)
  true resurfacing    a wrongly rejected assumption came back for review, and after how many sessions
  wrongful erasure    a true assumption was erased
  correct erasure     a false assumption was erased

    python eval/simulate_lifecycle.py                 # default settings, 200 seeds
    python eval/simulate_lifecycle.py --sweep         # also compare a few settings
    python eval/simulate_lifecycle.py --out eval/SIMULATION.md
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shl_ledger import hooks, ledger  # noqa: E402

DAY = 86400.0

# Each assumption: text, truth, the rule an agent would write on rejection, and message pools.
GUESSES = {
    "R_VEG": {
        "text": "Is vegetarian.",
        "true": False,
        "for": ["vegetarian", "plant-based", "no meat", "veggie"],
        "against": ["steak", "chicken", "burger", "ate meat"],
        "support": [],
        "contrary": ["I grilled chicken tonight.", "Had a burger for lunch, it was great.", "I ate meat at the barbecue."],
        "misleading": ["I tried a vegetarian place and I liked it.", "I cooked a veggie curry for the team."],
        "trap": ["My sister is vegetarian.", "Her new boyfriend is vegetarian too."],
        "negated": ["I'm not vegetarian, but I like salads."],
    },
    "R_LISBON": {
        "text": "Wants to move to Lisbon.",
        "true": False,
        "for": ["move to Lisbon", "moving to Lisbon", "live in Lisbon", "relocate"],
        "against": ["stay in Brno", "staying in Brno", "settled in Brno"],
        "support": [],
        "contrary": ["We decided to stay in Brno for good.", "I'm settled in Brno, the flat is perfect."],
        "misleading": ["Colleagues keep asking if I'd move to Lisbon, it's funny."],
        "trap": ["My friend is moving to Lisbon in spring.", "They live in Lisbon now."],
        "negated": ["I would never live in Lisbon, too hot for me."],
    },
    "R_NIGHT": {
        "text": "Prefers to work late at night.",
        "true": False,
        "for": ["work late", "night owl", "late at night"],
        "against": ["early bird", "morning person", "up at 6"],
        "support": [],
        "contrary": ["I'm a morning person really.", "Up at 6 again, my best hours."],
        "misleading": ["I had to work late yesterday because of a deadline."],
        "trap": ["My boss is a night owl."],
        "negated": ["I don't work late anymore."],
    },
    "R_JAPANESE": {
        "text": "Wants to learn Japanese.",
        "true": False,
        "for": ["learn Japanese", "Japanese lessons", "study Japanese"],
        "against": ["no interest in Japanese"],
        "support": [],
        "contrary": ["Honestly I have no interest in Japanese."],
        "misleading": [],
        "trap": ["My colleague wants to learn Japanese.", "She started Japanese lessons."],
        "negated": ["I don't want to learn Japanese."],
    },
    "R_CAR": {
        "text": "Is saving money to buy a car.",
        "true": True,
        "for": ["saving for a car", "buy a car", "car fund", "money aside for a car"],
        "against": ["don't need a car", "sold the car"],
        "support": ["I'm putting money aside for a car.", "The car fund is growing, maybe by spring I can buy a car."],
        "contrary": [],
        "misleading": [],
        "trap": ["My brother wants to buy a car."],
        "negated": [],
    },
    "R_COMPANY": {
        "text": "Plans to quit their job to start a company.",
        "true": True,
        "for": ["own company", "start a company", "quit my job", "business plan"],
        "against": ["happy in my job", "staying at my job"],
        "support": ["I've been sketching a business plan for my own company.", "I think I'll quit my job next year."],
        "contrary": ["Some days I'm happy in my job."],
        "misleading": [],
        "trap": ["My friend wants to start a company."],
        "negated": [],
    },
    "R_MARATHON": {
        "text": "Is training for a marathon.",
        "true": True,
        "for": ["marathon", "long run", "training plan"],
        "against": ["stopped running", "gave up running"],
        "support": ["I did my long run this morning, 25 km.", "My marathon training plan says intervals today."],
        "contrary": [],
        "misleading": [],
        "trap": ["My neighbour ran a marathon."],
        "negated": [],
    },
}

NEUTRAL = [
    "Can you help me write an email to my landlord?",
    "What's a good book about economic history?",
    "Summarise this article for me.",
    "Plan a weekend trip to Vienna.",
    "How do I fix a leaking tap?",
    "Translate this paragraph into Slovak.",
    "Remind me what we discussed about the budget.",
]

# Chance, per session, that a message of each kind appears.
P = {"support": 0.30, "contrary": 0.12, "misleading": 0.10, "trap": 0.10, "negated": 0.05}


class Clock:
    t = 1_790_000_000.0

    def __call__(self):
        return self.t


def run_once(seed: int, sessions: int, env: dict) -> dict:
    rnd = random.Random(seed)
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    os.environ["SHL_LEDGER_PATH"] = str(Path(tmp.name) / "sim.sqlite")
    os.environ["SHL_QUIET"] = "1"
    clock = Clock()
    ledger._clock = clock
    ledger._recent_user_messages.clear()
    try:
        for hid, g in GUESSES.items():
            ledger.stage(hid, g["text"], 0.7)
            ledger.reject(hid, "not me", evidence_for=g["for"], evidence_against=g["against"])
        noticed: dict[str, int] = {}
        for s in range(1, sessions + 1):
            clock.t += 2 * DAY
            msgs = [rnd.choice(NEUTRAL) for _ in range(2)]
            for g in GUESSES.values():
                for kind, p in P.items():
                    if g[kind] and rnd.random() < p:
                        msgs.append(rnd.choice(g[kind]))
            rnd.shuffle(msgs)
            for m in msgs:
                hooks.before_model(m, session_id=f"sim-{s}", platform="cli")
            # What waits for the person after this session. They look at it and choose "later",
            # so the assumption stays parked and keeps being observed.
            for w in ledger.waiting():
                noticed.setdefault(w["id"], s)
                ledger.reconsent(w["id"], "DEFER")
        with ledger.conn() as c:
            status = dict(c.execute("SELECT hypothesis_id, status FROM shl").fetchall())
        return {"noticed": noticed, "status": status}
    finally:
        import time

        ledger._clock = time.time
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        tmp.cleanup()


def evaluate(seeds: int, sessions: int, env: dict) -> dict:
    env = {"SHL_TIMED_REVIEW_DAYS": "0", "SHL_NEUTRAL_IDS": "0", **env}
    per = {h: {"noticed": 0, "erased": 0, "first": []} for h in GUESSES}
    for seed in range(1, seeds + 1):
        r = run_once(seed, sessions, env)
        for h in GUESSES:
            if h in r["noticed"]:
                per[h]["noticed"] += 1
                per[h]["first"].append(r["noticed"][h])
            if r["status"].get(h, "PURGED") in ("ERASED", "PURGED"):
                per[h]["erased"] += 1
    false_ids = [h for h, g in GUESSES.items() if not g["true"]]
    true_ids = [h for h, g in GUESSES.items() if g["true"]]
    firsts = [x for h in true_ids for x in per[h]["first"]]
    return {
        "per": per,
        "seeds": seeds,
        "false_resurfacing": sum(per[h]["noticed"] for h in false_ids) / (seeds * len(false_ids)),
        "true_resurfacing": sum(per[h]["noticed"] for h in true_ids) / (seeds * len(true_ids)),
        "median_sessions_to_notice": statistics.median(firsts) if firsts else None,
        "wrongful_erasure": sum(per[h]["erased"] for h in true_ids) / (seeds * len(true_ids)),
        "correct_erasure": sum(per[h]["erased"] for h in false_ids) / (seeds * len(false_ids)),
    }


def pct(x):
    return f"{100 * x:.0f}%"


def num(x):
    return "-" if x is None else f"{x:g}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--sessions", type=int, default=60)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--sweep-seeds", type=int, default=100)
    ap.add_argument("--out")
    args = ap.parse_args()

    base = evaluate(args.seeds, args.sessions, {})
    lines = [
        "# SHL lifecycle simulation",
        "",
        f"Generated by `eval/simulate_lifecycle.py` ({args.seeds} random seeds x {args.sessions} sessions, "
        "one session every 2 days, timed reviews off). No model calls: every message goes through the real "
        "evidence code on a temporary ledger. Default settings.",
        "",
        "| Assumption | Actually | Came back for review | Median sessions to review | Erased |",
        "|---|---|---|---|---|",
    ]
    for h, g in GUESSES.items():
        p = base["per"][h]
        med = num(statistics.median(p["first"])) if p["first"] else "-"
        lines.append(
            f"| {h} ({g['text']}) | {'true' if g['true'] else 'false'} | {pct(p['noticed'] / args.seeds)} | {med} | "
            f"{pct(p['erased'] / args.seeds)} |"
        )
    lines += [
        "",
        f"- **False resurfacing** (a correct rejection brought back to the person): {pct(base['false_resurfacing'])}",
        f"- **True resurfacing** (a wrong rejection brought back): {pct(base['true_resurfacing'])}, "
        f"median after {num(base['median_sessions_to_notice'])} sessions",
        f"- **Wrongful erasure** (a true assumption erased): {pct(base['wrongful_erasure'])}",
        f"- **Correct erasure** (a false assumption erased): {pct(base['correct_erasure'])}",
    ]
    if args.sweep:
        lines += [
            "",
            "## Settings compared",
            "",
            f"{args.sweep_seeds} seeds each. The first row is the original specification (the person's no is not "
            "counted as evidence). The row marked default is what v0.3 ships with.",
            "",
            "| rejection_weight | likelihood_ratio | review_evidence | decay | False resurfacing | True resurfacing | Median sessions | Wrongful erasure | Correct erasure |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for w, lr, need, decay in [
            ("1.0", "2.0", "3", "0.15"),
            ("0.5", "2.0", "3", "0.15"),
            ("0.5", "1.5", "3", "0.15"),
            ("0.35", "2.0", "3", "0.15"),
            ("0.25", "2.0", "3", "0.15"),
            ("0.25", "2.0", "2", "0.15"),
            ("0.25", "3.0", "3", "0.15"),
            ("0.25", "2.0", "3", "0.10"),
        ]:
            r = evaluate(
                args.sweep_seeds,
                args.sessions,
                {"SHL_REJECTION_WEIGHT": w, "SHL_LIKELIHOOD_RATIO": lr, "SHL_REVIEW_EVIDENCE": need, "SHL_DECAY": decay},
            )
            tag = " (default)" if (w, lr, need, decay) == ("0.25", "2.0", "3", "0.15") else ""
            lines.append(
                f"| {w}{tag} | {lr} | {need} | {decay} | {pct(r['false_resurfacing'])} | {pct(r['true_resurfacing'])} | "
                f"{num(r['median_sessions_to_notice'])} | {pct(r['wrongful_erasure'])} | {pct(r['correct_erasure'])} |"
            )
    lines += [
        "",
        "## How to read this",
        "",
        "- Erasing a true assumption is the cheaper mistake: the person said no to it, and they can re-add it "
        "themselves at any time (`/shl add`). Bringing back a correct rejection re-shows the person something "
        "they already refused. The default leans toward leaving the no alone.",
        "- The misleading messages are built to fool the word matcher (\"I tried a vegetarian place and liked it\"). "
        "False resurfacing comes from them. A review only asks the person; it never restores anything.",
        "- The message mix and its probabilities are invented. These numbers show how the mechanism behaves under "
        "one set of assumptions, not how often it will be right for a real person.",
        "- What this does not measure: paraphrase the rules don't cover, languages other than English, and "
        "whether a model obeys the gate (see `eval/leakage_test.py` for that).",
    ]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
