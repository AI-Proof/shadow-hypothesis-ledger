# Shadow-Hypothesis Ledger (SHL)

**When you tell your AI agent "no, that's not me", it should stop using that assumption. Not just this time, and not only if it remembers.**

Long-running AI agents build up assumptions about the person they work for: goals, habits, plans, preferences, how to act with them. Technically these are inferences the model drew from what the person said and did. Some are wrong. When the person says so, agents today usually do one of two things:

1. **Delete the assumption.** The agent forgets the "no" too, and often infers the same thing again next week.
2. **Keep it somewhere** (a memory file, a summary, the chat history) with a note not to use it. The assumption is still in front of the model, and models don't always obey the note.

SHL is a third option: **keep the rejected assumption, but take it out of the prompt.** Rejected assumptions are stored in a small local database and shown to the model by id only, never by text. What the person says later can make a rejected assumption fade away for good, or, if the evidence builds up, bring it back to the person as a question. Only the person can restore it.

It's a small, local, dependency-free Python library with a ready-made plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). The ledger works with any agent framework.

**Status: v0.3, working prototype.** v0.3 runs on the author's own Hermes profile, in trial mode (evidence logged, nothing changed) since 27 September 2026. What is proven and what isn't is under [What this does and doesn't prove](#what-this-does-and-doesnt-prove).

## Install on Hermes

```
hermes plugins install https://github.com/AI-Proof/shadow-hypothesis-ledger#shl_ledger
hermes plugins enable shl-ledger
```

Start a new session, then type `/shl`. Keep the `#shl_ledger` part: without it Hermes installs the whole repository, which isn't a plugin, and shows only a warning. Plugins are per profile; add `-p <profile>` for another one.

To try it without changing anything, set `evidence_mode: "log"` first ([config example](examples/config.example.yaml)), and read `hermes shl report` after a few days. More in [docs/HERMES.md](docs/HERMES.md).

## What it looks like

You tell the agent it assumed something wrong. It parks the assumption, and the reply ends with:

```
░▒▓█ ᯽ SHL ᯽ █▓▒░

Parked 1 assumption you rejected (#1). It won't be used.
Changed your mind? "/shl use 1"

░▒▓███• ❉ •███▓▒░
```

From then on, every model call carries a block like this ([example](examples/inject_block.example.txt)). The model sees that something was rejected, never what; even the id is neutral:

```
SHL GATE (rules in the system prompt). Use only ACTIVE items; parked items are ids only.
ACTIVE 2:
- HYP_001: Lives in Brno.
- HYP_002: Prefers written messages to phone calls.
PARKED 4 (ids only; not to be used): SHL_8S75NM, SHL_E5Y3JG, SHL_UKKPJY, SHL_87JZNE
```

Every new session starts with one short note ([examples](examples/session_note.example.txt)). It carries counts, never the text of an assumption:

```
░▒▓█ ᯽ SHL ᯽ █▓▒░

Since your last session: 1 assumption you once rejected has crossed the confidence threshold (0.65), and is waiting for your reconsideration.
1 assumption quietly decayed and was purged.
Type "/shl" to see them and decide. Until you do, nothing changes and the assistant won't use them.

░▒▓███• ❉ •███▓▒░
```

When nothing needs you, it says so: *"4 assumptions you rejected are parked in your Shadow-Hypothesis Ledger. No actions necessary."*

`/shl` shows you (and only you) what's waiting ([example](examples/shl_command.example.txt)):

```
░▒▓█ ᯽ SHL ᯽ █▓▒░

WAITING FOR YOU
 1. "Prefers to work late at night."
    0.37 → 0.82 · 3 supporting mentions

/shl use 1     use it again
/shl later 1   keep it parked, ask me again later
/shl drop 1    erase it for good
/shl 1         show why (the evidence)
/shl all       everything parked (3)

░▒▓███• ❉ •███▓▒░
```

## How it works

Every assumption about the person is a row with a status:

| Status | Meaning | Reaches the prompt? |
|---|---|---|
| `ACTIVE` | usable | yes, as text |
| `SHADOW` | the person rejected it (parked, numbered #1, #2, ...) | a neutral id only |
| `REINFORCED` | parked, and the person's later messages seemed to support it | a neutral id only |
| `ERASED` | gone; only a one-way fingerprint remains so the agent can't add it again | no |

```
stage             → ACTIVE
reject            → SHADOW       (neutral id, number, evidence rule; weak assumptions are erased at once)
your messages     → confidence up or down, by plain code
   below 0.30     → ERASED       (silently; counted in the next session note)
   3 sessions up  → waiting for you (session note, then /shl)
/shl use 1        → ACTIVE       (the only way back, and only you can do it)
/shl later 1      → SHADOW       (ask again in 90 days at the earliest)
/shl drop 1       → ERASED
```

**Evidence rules.** When an assumption is rejected, it gets a short rule: phrases that would support it if you said them about yourself later, and phrases that would contradict it. For "Is vegetarian": `vegetarian`, `plant-based`, `no meat` for; `steak`, `chicken` against. The agent writes the rule as part of the rejection (it just talked with you about it), or SHL builds one from the assumption's own words. You can change any rule with `/shl rule`.

**Checked on every message you send, by plain code.** No model call, no tokens. Only your own messages count: never the assistant's replies, tool results or background jobs, so an agent can't talk an assumption back into existence. "I'm not vegetarian" (or "No soy vegetariano", "Nie som vegetarián") counts against; "my friend is vegetarian" doesn't count at all. At most one piece of evidence per assumption per session.

**The numbers.** Your "no" itself is evidence: it makes the assumption four times less likely (0.70 becomes 0.37). Each supporting mention doubles the odds (0.37, 0.54, 0.70, 0.82). Each contrary mention subtracts 0.15. Below 0.30 it is erased. After supporting mentions in 3 separate sessions, and back above 0.65, it waits for your review. So does any parked assumption 90 days after you rejected it. These are Bayesian updates with hand-picked numbers, not fitted ones; [the simulation](eval/SIMULATION.md) shows how they trade off.

**Only you decide about a rejected assumption.** With `/shl use | later | drop`, from the terminal, or by telling the assistant a line with the code that `/shl 1` shows you (`use 1 K7Q2`). Without that, the agent's tool can't restore, park again, erase or forget a rejected assumption, and re-rejecting one can't reset its evidence. Evidence alone never makes an assumption usable.

**The other doors.** A rejected assumption could also come back through memory the agent saves, a summary or the chat history. SHL screens memory writes automatically and replaces sentences that repeat a rejected assumption with `[parked: ID]`. A `GHOST` line tells the agent when your current message touches a rejected assumption, so it asks instead of assuming.

**Record keeping.** Every change is logged with the confidence before and after, in a hash-chained log that never holds the text of an assumption or your reasons. `/shl verify` checks the chain. `/shl 1` shows you the evidence behind an assumption, with your own sentences and dates.

## Commands

Everyday, with the number from `/shl`:

| Command | What it does |
|---|---|
| `/shl` | what's waiting for you |
| `/shl 1` | the story of #1: why it came back, in your own words |
| `/shl use 1` | use it again |
| `/shl later 1` | keep it parked, ask again later |
| `/shl drop 1` | erase it for good |
| `/shl all` | every parked assumption |
| `/shl help all` | the other commands |

The others (`active`, `add`, `reject`, `forget`, `rule`, `check`, `report`, `history`, `verify`, `settings`, `rule ALL auto`, `rename ALL`) are listed by `/shl help all`. The older words `review` and `reconsent 1 REVIEW|DEFER|DELETE` still work. Everything works from the terminal too: `hermes shl report`.

## Settings

In the Hermes plugin settings, in `config.yaml` under `plugins.entries.shl-ledger.settings`, or as `SHL_<NAME>` environment variables.

| Setting | Default | Effect |
|---|---|---|
| `evidence_mode` | `on` | `log` records what would happen and changes nothing; `off` ignores your messages |
| `session_note` | on | the SHL note at the start of each session |
| `confirmations` | on | the short SHL line at the end of a reply when something happened |
| `ghost_mode` | `ask` | `inert`: the agent never offers to restore a rejected assumption |
| `timed_review_days` | 90 | a parked assumption waits for your review after this many days, even without evidence; 0 = never |
| `purge_mode` | `hash` | `full` deletes erased rows entirely, fingerprint included |
| `rule_source` | `agent,auto` | add `llm` for one model call per rejection to write the rule |

Advanced (`rejection_weight`, `likelihood_ratio`, `decay`, `expire_at`, `review_evidence`, `notice_threshold`, `shadow_threshold`, `neutral_ids`, `screen_memory`, `memory_tools`, `gate_note`) are described in [`plugin.yaml`](shl_ledger/plugin.yaml) and [docs/SPEC.md](docs/SPEC.md).

## Use it without Hermes

```
pip install git+https://github.com/AI-Proof/shadow-hypothesis-ledger
```

```python
from shl_ledger import hooks, ledger

ledger.stage("HYP_CITY", "Lives in Brno.")
ledger.stage("HYP_NIGHT", "Prefers to work late at night.")
ledger.reject("HYP_NIGHT", "not true", evidence_for=["night owl", "work late"], evidence_against=["morning person"])

block = hooks.before_model(user_message, session_id=session_id)   # score the message, build the gate
prompt = system_prompt + "\n\n" + block
reply = your_model(prompt, user_message)
reply = hooks.after_model(reply, session_id=session_id) or reply  # session note and confirmations
new_args = hooks.before_tool("memory", args)                      # screened memory write, or None
```

The database lives at `$SHL_LEDGER_PATH`, else `$HERMES_HOME/ledger/shl.sqlite`, else `./ledger/shl.sqlite`. Requires Python 3.10+. No other dependencies.

## Upgrading from v0.2

`hermes plugins update shl-ledger` if you installed with the command above; otherwise install it that way once. The database upgrades itself and first copies itself to `ledger/backups/`. Existing rejected assumptions keep their confidence and their old ids, and have no evidence rule yet: run `/shl rule ALL auto` and `/shl rename ALL`. Details in [docs/HERMES.md](docs/HERMES.md#upgrading-from-v02).

## Tests

```
python -m pytest -q
```

147 tests, each on a temporary database with a controllable clock, run on Linux, Windows and macOS. They cover the original guarantees (rejected text never in the block, only the person restores, evidence never promotes), the evidence lifecycle, the session note and commands, consent codes, neutral ids, memory screening, non-Latin text, the change log, upgrades from older databases, and the Hermes plugin called with the exact arguments Hermes passes.

## What this does and doesn't prove

**Demonstrated by the tests:** rejected text and evidence rules never enter the gate block, the agent's tool results or the session note, and rejected assumptions carry neutral ids; only the person decides about a rejected assumption; evidence never promotes; a rejected assumption can't be re-added by the agent under a new id with the same or near-identical wording, even after erasure; erased text is gone from the table, and the log never had it; only the person's own messages are scored.

**Measured in simulation** ([eval/SIMULATION.md](eval/SIMULATION.md)): for an invented person with seven rejected assumptions and 60 sessions of scripted messages, default settings brought back 87% of the wrongly rejected assumptions (median after 9 sessions) and 10% of the correctly rejected ones. They erased 96% of the false assumptions, and also 13% of the true ones (which the person can re-add). Without counting the person's "no" as evidence, as in the original specification, 30% of correct rejections came back. The messages and their mix are invented, and some are built to fool the matcher. This compares settings; it doesn't predict accuracy for a real person.

**Not yet demonstrated:** that a model given the SHL block actually uses rejected assumptions less often than a model given an instruction not to. That's a question about model behaviour, and the tests can't answer it. [`eval/leakage_test.py`](eval/) measures it under six conditions. Results will be published here when they exist. Until then: **ledger isolation demonstrated; prompt isolation hypothesised.**

Also not covered: text the agent already copied elsewhere before the rejection, true paraphrase, negation in languages beyond the built-in lists (English and Slovak, plus basic Czech, Russian, Polish, Spanish, Portuguese, German and Italian; rules themselves work in any script), and any security boundary. SHL keeps an honest agent honest; it doesn't stop a hostile one. The full list is in [docs/HONESTY.md](docs/HONESTY.md).

More detail: [docs/SPEC.md](docs/SPEC.md), [docs/DESIGN_NOTES.md](docs/DESIGN_NOTES.md), [docs/HERMES.md](docs/HERMES.md).

## Why this matters

As agents get longer memories, the question stops being "what does the model know about me?" and becomes "what happens when I say it's wrong?". SHL's answer is one rule: **the model gets what the person has let stand, and the person's "no" is kept, respected, and reversible only by them.** It's the same rule the author applies elsewhere: other systems get your chosen action, never the model of you behind it.

## Author, licence and prior art

Created by **Daniel Bulla**, Bratislava. Copyright 2026 Daniel Bulla.

Licensed under the **Apache License 2.0** ([LICENSE](LICENSE), [NOTICE](NOTICE)). Anyone may use, change and share SHL, including commercially, as long as they keep the copyright and NOTICE file, which credit the author. The licence also includes a patent clause: everyone who contributes grants users a licence to any of their patents that cover their contribution, and anyone who sues claiming SHL infringes a patent loses the patent licence they received under it.

**Prior art.** This repository, dated by its public history from 26 September 2026, is a public disclosure of the method: rejected inferences about a person are kept but excluded from the model's prompt, shown only by id, and refused if re-added under another id; each carries an evidence rule written at rejection time and checked without model calls against the person's own later messages; the rejection itself and each later mention update its confidence by Bayesian odds updates, with contrary evidence lowering it until it is erased; the person is told at the start of a session when support builds up; and only the person's explicit re-consent restores it, where an agent acting on the person's behalf must show that the person's own message carried a code shown only to the person. It is published so that the method stays free to use.

Built with the help of AI assistants, which wrote parts of the code under the author's direction; every change was reviewed and tested.

Feedback, issues and results from other models are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
