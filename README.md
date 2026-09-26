# Shadow-Hypothesis Ledger (SHL)

**When you tell your AI agent "no, that's not me", it should stop using that guess. Not just this time, and not only if it remembers.**

Long-running AI agents build up guesses about the person they work for: goals, habits, plans, preferences. Some guesses are wrong. When the person says so, agents today usually do one of two things:

1. **Delete the guess.** The agent forgets the "no" too, and often works out the same guess again next week.
2. **Keep it somewhere** (a memory file, a summary, the chat history) with a note not to use it. The guess is still in front of the model, and models don't always obey the note.

SHL is a third option: **keep the rejected guess, but take it out of the prompt.** Rejected guesses are stored in a small local database and shown to the model by id only, never by text. They come back only when the person says so.

It's a small, local, dependency-free Python library with a ready-made plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). The ledger itself works with any agent framework.

**Status: working prototype, v0.2.** The previous version runs daily on the author's own Hermes profile; v0.2 adds the protections listed in the [changelog](CHANGELOG.md). What is proven and what isn't is under [What this does and doesn't prove](#what-this-does-and-doesnt-prove).

## How it works

Every guess about the person is a row with a status:

| Status | Meaning | Reaches the prompt? |
|---|---|---|
| `ACTIVE` | usable | yes, as text |
| `SHADOW` | the person rejected it | id only |
| `REINFORCED` | rejected, and later evidence seemed to support it | id only |
| `EXPIRED` | rejected, and evidence ran against it, or the person said DELETE | id only |
| `ERASED` | the person asked to forget it; the text is gone | no |

```
stage              → ACTIVE
reject             → SHADOW
reconsent REVIEW   → ACTIVE      (the only way back, and only the person can do it)
reconsent DEFER    → SHADOW
reconsent DELETE   → EXPIRED
forget             → ERASED      (text erased; a one-way hash stays so it can't be re-added)
corroborate        → REINFORCED  (optional, off by default; never promotes)
counter            → SHADOW or EXPIRED (optional, off by default)
```

Before every model call, the Hermes plugin's `pre_llm_call` hook adds a block like this ([example](examples/inject_block.example.txt)):

```
SHL GATE. Guesses about the person that may be used are listed as ACTIVE. Guesses the person rejected are listed by id only.
Use only ACTIVE items. Do not guess at, reconstruct or bring back parked items. Only the person can restore one (reconsent REVIEW).
ACTIVE 2:
- HYP_CITY: Lives in Brno.
- HYP_WRITTEN: Prefers written messages to phone calls.
PARKED 2 (ids only; not to be used): HYP_NIGHT, HYP_MOVE
```

Because the hook runs on every call, isolation doesn't depend on the model remembering to look anything up.

### Why keep the rejected guess at all?

- **So it can't sneak back.** If an agent tries to add the same guess again, even under a new id or reworded slightly, the ledger refuses it and points to the rejected one.
- **So the person stays in charge.** A rejected guess can be reviewed and restored later, by the person, with one command.
- **So there's a record.** Every change is logged: when, which id, from which status to which, and the person's short reason for a rejection. The log never stores the guess itself.

And if the person wants it gone, `forget` erases the text and the reasons in the log. One thing stays: a one-way fingerprint (hash) of the wording, so the same guess can't be added again. For a very short guess, someone with the database file could work out the wording by trying candidates, so treat erased guesses as hidden, not unrecoverable.

### The other doors: memory, summaries, chat history

The prompt gate only controls the SHL block. A rejected guess can still come back through a memory file the agent writes, a session summary, or the chat history. SHL gives the agent two tools for this:

- `screen(text)` checks text before it's saved and replaces sentences that repeat a rejected guess with `[parked: ID]`. It also removes things that look like API keys.
- **Ghost detection:** if the person's current message repeats a rejected guess, the block adds a `GHOST` line so the agent asks rather than assumes.

Both use simple word matching (exact and near-exact wording, light stemming). They catch reworded sentences with most of the same words; they miss real paraphrases. They are seatbelts, not guarantees.

## Install

Requires Python 3.10+. No other dependencies.

**As a library (any agent):**

```python
from shl_ledger import ledger, inject, screen

ledger.stage("HYP_CITY", "Lives in Brno.")
ledger.stage("HYP_NIGHT", "Prefers to work late at night.")
ledger.reject("HYP_NIGHT", "not true")

system_prompt += "\n\n" + inject.build_context(user_message)
safe_note = screen.screen(summary_text)["text"]
```

The database lives at `$SHL_LEDGER_PATH`, else `$HERMES_HOME/ledger/shl.sqlite`, else `./ledger/shl.sqlite`.

**As a Hermes Agent plugin:** copy the `shl_ledger` folder into your profile's plugins folder as `shl-ledger`, then:

```
hermes -p <profile> plugins enable shl-ledger --no-allow-tool-override
```

Start a new session. Keep `memory.write_approval: true` in your profile so guesses don't silently become memory ([config example](examples/config.example.yaml)). The agent gets an `shl` tool, a `/shl` slash command and an `shl-context` skill.

**Upgrading from the technical preview:** back up your `shl.sqlite` first; the database upgrades itself on first use. Evidence tracking (`corroborate`, `counter`) is now off by default; set `SHL_EVIDENCE=1` to keep using it.

**Settings (environment variables):**

| Variable | Default | Effect |
|---|---|---|
| `SHL_EVIDENCE` | off | `1` turns on `corroborate` and `counter` |
| `SHL_GHOST_MODE` | `ask` | `inert` stops the agent from offering to restore a parked guess, for rejections that must stay final |
| `SHL_GATE_NOTE` | off | `1` adds a line reminding the model that the block is a filter, not proof |
| `SHL_LEDGER_PATH` | see above | where the database lives |

## Commands

| Slash command | What it does |
|---|---|
| `/shl` | list ACTIVE guesses and parked ids |
| `/shl stage ID text` | add a guess |
| `/shl reject ID why` | park a guess |
| `/shl reconsent ID REVIEW` | restore it (or `DEFER`, `DELETE`) |
| `/shl forget ID` | erase its text for good |
| `/shl review` | parked guesses that may deserve another look (never promoted automatically) |
| `/shl history [ID]` | change log |

## Tests

```
python -m pytest -q        # or: python tests/test_shl.py
```

25 tests, each on a temporary database; they pass on Linux and Windows. They cover the original guarantees (rejected text never in the block, only REVIEW promotes, evidence never promotes) and the v0.2 additions (renamed guesses refused, erasure, ghost detection on reworded messages, screening, upgrading an old database).

## What this does and doesn't prove

**Demonstrated by the tests:** rejected text never enters the SHL block; only the person's REVIEW brings a guess back; scores never promote; a rejected guess can't be re-added under a new id with the same or near-identical wording; erased text is gone from the table and the log.

**Not yet demonstrated:** that a model, given the SHL block, actually uses rejected guesses less often than a model given an instruction not to. That's a question about model behaviour, and the tests above can't answer it. [`eval/leakage_test.py`](eval/) measures it: the same questions under six conditions, from "rejected guess still in memory" to "SHL block", scored by a separate judge call. Results will be published here when they exist. Until then: **ledger isolation demonstrated; prompt isolation hypothesised.**

Also not covered: text the agent already copied elsewhere before the rejection, true paraphrases, concurrent writers beyond SQLite's own locking, and any security boundary. SHL keeps an honest agent honest; it doesn't stop a hostile one.

The scores (`posterior_confidence`, `evidence_counter`) are simple labels (+0.05, −0.15, floor 0.30). They are placeholders, not a fitted Bayesian model, and nothing depends on them.

More detail: [docs/SPEC.md](docs/SPEC.md) and [docs/HONESTY.md](docs/HONESTY.md).

## Why this matters

As agents get longer memories, the question stops being "what does the model know about me?" and becomes "what happens when I say it's wrong?". SHL's answer is one rule: **the model gets what the person has let stand, and the person's "no" is kept, respected, and reversible only by them.** It's the same rule the author applies elsewhere: other systems get your chosen action, never the model of you behind it.

## Author, licence and prior art

Created by **Daniel Bulla**, Bratislava. Copyright 2026 Daniel Bulla.

Licensed under the **Apache License 2.0** ([LICENSE](LICENSE), [NOTICE](NOTICE)). Anyone may use, change and share SHL, including commercially, as long as they keep the copyright and NOTICE file, which credit the author. The licence also includes a patent clause: everyone who contributes grants users a licence to any of their patents that cover their contribution, and anyone who sues claiming SHL infringes a patent loses the patent licence they received under it.

**Prior art.** This repository, dated by its public history from 26 September 2026, is a public disclosure of the method: rejected inferences about a person are kept but excluded from the model's prompt, shown only by id, refused if re-added under another id, and restored only by the person's explicit re-consent. It is published so that the method stays free to use.

Built with the help of AI assistants, which wrote parts of the code under the author's direction; every change was reviewed and tested.

Feedback, issues and results from other models are welcome.
