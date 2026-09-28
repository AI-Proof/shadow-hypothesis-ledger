# Design notes

Why SHL works the way it does, and where each part comes from.

## Where it comes from

SHL is one piece of a larger design by Daniel Bulla for personal AI that keeps the model of a person with that person: other systems get the person's chosen action, never the profile behind it. The author's technical specification of August 2026 (not published) describes a "state-segregated memory" for rejected inferences. v0.3 implements that section. The map below lists each element and where it lives in the code.

| Specification element | v0.3 | Where |
|---|---|---|
| A rejected inference with confidence of at least 0.65 becomes a shadow hypothesis | `shadow_threshold` (0.65); below it the inference is erased with its fingerprint kept | `ledger.reject` |
| Shadow hypotheses are excluded from every prompt-building routine | the gate lists ids only; the agent's tool never returns parked text; memory writes are screened | `inject.py`, `tools.handle`, `hooks.before_tool` |
| Full rejection record: text, confidence, time, context | text, confidence before and after the no, time, reason, source note, session and turn ids | `ledger.reject` |
| A machine-checkable evidence predicate with corroborating and counter patterns, derived at rejection | the evidence rule: `for` and `against` phrases, from the agent, a one-off model call, or the inference's own words | `rules.py`, `ledger._build_rule` |
| Bayesian reinforcement on corroborating episodic events | odds update with likelihood ratio L, run by plain code on each of the person's messages | `scoring.support`, `ledger.observe` |
| Linear decay of 0.15 per counter-evidence incident | same | `scoring.contradict` |
| Auto-expiry below 0.30 | erased silently (`purge_mode=hash` keeps the fingerprint; `full` deletes the row) | `ledger._erase` |
| REINFORCED once there is corroborating evidence | same | `ledger._apply` |
| Meta-reflection trigger: 3 pieces of evidence, or 90 days | waits for the person when support ≥ 3 and confidence ≥ 0.65, or on the timed review date | `ledger._queue_due` |
| Re-consent interface: the hypothesis, the confidence and its trajectory, the evidence, and Review / Defer / Delete | the session note points to `/shl`; `/shl 55` shows the inference, its confidence history and evidence; `/shl use / later / drop 55` | `notices.py`, `tools._detail_text` |
| Evidence never changes the access state by itself; re-activation needs an explicit user authorisation | REVIEW only from the person: the slash command, the CLI, or a consent code the person typed | `ledger.reconsent` |
| Audit log of every transition with confidence values | append-only, hash-chained, text-free | `ledger._event`, `ledger.verify_log` |

## Decisions that go beyond the specification

**The person's "no" counts as evidence.** The specification keeps a rejected inference at the confidence it had before the rejection. In simulation that brought correct rejections back to the person about 30% of the time: a few misleading mentions ("I tried a vegetarian place") were enough to trigger a review of something the person had already refused. v0.3 applies the rejection as a Bayesian update first (`rejection_weight` 0.25: the inference becomes four times less likely). That cut the false-review rate to about 10% while still bringing back 87% of wrongly rejected inferences. See [`eval/SIMULATION.md`](../eval/SIMULATION.md). Setting `rejection_weight: 1` restores the original behaviour.

**Up by odds, down by steps.** Support uses a Bayesian odds update, so each mention counts for less as confidence rises. Contrary evidence uses the specification's fixed step. The asymmetry is deliberate: it takes several mentions to raise a rejected inference, and a clear contrary statement is enough to retire it.

**Erase, but keep a fingerprint.** The specification deletes everything below 0.30. Then the same inference could be re-inferred and added as new next week, which is the failure SHL exists to prevent. By default the text, rule, reason and evidence go, and only a one-way hash remains. `purge_mode: full` is the literal version. The person can always re-add an erased inference themselves (`/shl add`); only the agent is blocked.

**Only the person's own words count.** Evidence comes from the person's message in `pre_llm_call`, never from the assistant's replies, tool results, cron jobs or subagents. Otherwise an agent that keeps hinting at an inference would create its own evidence.

**One piece of evidence per inference per session.** Ten mentions in one rant are one data point.

**Rules are written once, checked forever without a model.** The only model call is optional, once per rejection. Every per-message check is string matching that runs in well under a millisecond per inference.

**Neutral ids.** An agent names inferences as it likes, and `HYP_VEGETARIAN` in the parked list tells the model exactly what was rejected. On rejection SHL renames the inference to a neutral id (`SHL_7Q2KX9`). The agent gets the new id in the tool result of that turn, when the inference is in the conversation anyway.

**The agent can't overrule a pending review.** Re-rejecting a parked inference changes nothing, and the agent's tool can't defer, delete or forget a rejected inference without the person's code. Otherwise an agent could quietly cancel the question the person was about to be asked.

**Consent codes.** The specification says only the person can re-activate an inference. In a chat, the agent's tool is the natural way to act, and an agent could call it on its own. A 4-character code shown only by `/shl 55` (whose output never reaches the model), typed back by the person in the same session within 30 minutes (`use 55 K7Q2`), makes "the person decided" something the code can check.

**One note per session, never the text.** An earlier design put each due inference, with its text, in front of the next reply. That put rejected text back into the chat history the model reads, and a heavy user with many parked inferences could get one after another. Now each session starts with one short note of counts ("1 assumption ... is waiting for your reconsideration"), and the text appears only in `/shl`, which goes to the person alone. The person decides when to look.

**Numbers for people, neutral ids for the model.** The person types `/shl use 55`; the model sees `SHL_7Q2KX9`. Numbers are stable and never reused, so a command can't hit the wrong inference because a list changed in between.

**One frame for everything SHL says.** The session note, confirmations and `/shl` output share one banner (`░▒▓█ ᯽ SHL ᯽ █▓▒░` ... `░▒▓███• ❉ •███▓▒░`) with blank lines inside, so the person always knows what came from the ledger and what from the assistant, and the system prompt tells the model that framed sections are not its to repeat. The two ornaments (᯽ and ❉) always have a space on each side: many terminal fonts lack them, and the fallback font draws them wider than one cell, so a character right next to one gets overdrawn (seen in Windows Terminal).

**Log mode.** A new mechanism that changes what the agent sees should be observable before it acts. `evidence_mode: log` runs everything, records what would have happened, and changes nothing.

## Known weaknesses

- Word matching misses paraphrase and can be fooled by mentions that aren't about the person ("I tried a vegetarian place"). The first-person check catches only the obvious cases.
- Negation handling covers common forms in English and Slovak, not all languages.
- The likelihood ratio, the decay and the rejection weight are chosen, not fitted. The simulation shows how they trade off under invented assumptions.
- The fingerprint of a short inference can be recovered by guessing candidate wordings. Erased inferences are hidden, not unrecoverable.
- Ids stay in the change log after erasure, including the id an inference had before it was rejected. Stage with neutral ids (`HYP_014`) if that matters.
