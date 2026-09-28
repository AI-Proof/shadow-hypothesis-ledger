# Changelog

## 0.3.0 (September 2026)

Rejected assumptions (the model's inferences about the person) now learn from what the person says later, without model calls. The full lifecycle from the author's specification is implemented, plus a few protections it didn't have.

**Evidence and scoring**
- **Evidence rules.** Each rejected assumption gets a short rule: phrases that would support it and phrases that would contradict it. The agent supplies them when it rejects the assumption; otherwise they're built from the assumption's own words, or (optionally) by one model call. The person can edit any rule with `/shl rule`.
- **Checked by plain code on every message the person sends.** No model calls. Only the person's own messages count: never the assistant's replies, tool results, cron jobs or subagents. At most one piece of evidence per assumption per session. Handles negation ("I'm not vegetarian") and skips sentences about other people ("my friend is vegetarian"). Negation and person words for English and Slovak, plus a basic set for Czech, Russian, Polish, Spanish, Portuguese, German and Italian; rules work in any script.
- **Bayesian scoring.** Support raises an assumption's confidence by an odds update; contrary evidence lowers it by a fixed step (0.15). Below 0.30 the assumption is erased silently.
- **The person's "no" counts as evidence** (`rejection_weight`, default 0.25). In simulation this cut the rate at which correct rejections were brought back from 30% to 10%, while 87% of wrong rejections still came back. `rejection_weight: 1` gives the original specification's behaviour.
- **Assumptions rejected with low confidence** (below `shadow_threshold`, 0.65) are erased at once instead of parked.

**Notices and consent**
- **Reviews.** When the person's messages support a rejected assumption in three separate sessions and its confidence has recovered (0.65), or 90 days after the rejection, it waits for the person's decision: use it again, keep it parked (later), or erase it (drop).
- **The session note.** Every new session starts with one short SHL note: what is waiting, what was quietly purged, how many assumptions are parked, or "No actions necessary". Counts only, never assumption text. It also covers trial mode, evidence switched off, an upgraded ledger, a damaged change log and an unreadable database.
- **Confirmations.** A short SHL line at the end of a reply when something happened: an assumption was parked (numbered), a weak one was erased, the assistant tried to decide something without the person, or tried to add back a rejected assumption.
- **The SHL frame.** Everything SHL shows is wrapped in `░▒▓█ ᯽ SHL ᯽ █▓▒░` ... `░▒▓███• ❉ •███▓▒░`, with blank lines inside.
- **Short commands with numbers.** Parked assumptions get stable numbers (#55). Everyday use is `/shl`, `/shl 55`, `/shl use 55`, `/shl later 55`, `/shl drop 55`, `/shl all`, `/shl help`; everything else is under `/shl help all`. The older words still work.
- **Consent codes.** The agent's tool can decide about a rejected assumption only if the person typed a line like `use 55 K7Q2`, with the code that `/shl 55` showed them, in the same session. The slash command and the CLI always act for the person. The agent's tool can't `forget` a rejected assumption, and re-rejecting one changes nothing, so an agent can't cancel a review the person is about to see.
- **Neutral ids.** On rejection an assumption is renamed to a neutral id (`SHL_7Q2KX9`), so the parked list in the prompt doesn't hint at what was rejected (`neutral_ids`, on by default). `/shl rename ALL` does the same for older ledgers. Ids are now validated (1-64 of `A-Z a-z 0-9 _ . : -`, not only digits, so they can never be mistaken for a number).
- **The person can re-add an erased assumption** with `/shl add`. Only the agent is blocked by the fingerprint.

**Other doors**
- **Memory writes are screened automatically** through Hermes's `pre_tool_call` hook. Only the text being saved is screened; `old_text`, which Hermes uses to find an entry, is left alone.
- **The fixed rules move into a system-prompt section** (Hermes), so the per-turn block is shorter and the prompt cache stays warm.

**Record keeping**
- **Hash-chained change log** with confidence values at every change. `/shl verify` checks it. The log never holds assumption text or reasons; reasons live on the assumption and are erased with it.
- **Evidence table**: every piece of evidence with the phrase, a short excerpt of the person's sentence and the confidence before and after. `/shl 55` shows it to the person.
- **Log mode** (`evidence_mode: log`): runs everything, records what would have happened, changes nothing. `hermes shl report` summarises it.
- **Automatic backup** before a schema upgrade. v0.1 and v0.2 databases upgrade in place.

**Hermes plugin**
- One-command install: `hermes plugins install https://github.com/AI-Proof/shadow-hypothesis-ledger#shl_ledger`.
- Settings in the Hermes plugin settings (`config_schema`), with environment variables still working.
- New hooks: `transform_llm_output` (session note and confirmations) and `pre_tool_call` (memory screening). All hooks fail safe.
- `/shl` shows in Telegram's command menu (`args_hint` no longer starts with `<`).
- Handles multimodal messages (text parts only).
- Waits at most 5 seconds for a locked database, well inside Hermes's hook limit; every connection takes the write lock first, so the log's chain can't fork.
- Advanced commands: `active`, `add`, `rule`, `check`, `report`, `verify`, `settings`, `rule ALL auto`, `rename ALL`.

**Changes to note**
- `corroborate` and `counter` are no longer actions of the agent's tool. Evidence comes from the person's messages; the person can still record it by hand with `/shl corroborate ID` and `/shl counter ID`.
- Evidence tracking is on by default (it was opt-in in 0.2). `SHL_EVIDENCE=0` still turns it off.
- `reconsent DELETE` now erases the assumption (it used to mark it EXPIRED). REVIEW restores the assumption's pre-rejection confidence and drops the rejection reason.
- Text in any script is now matched and fingerprinted (v0.2 kept only a-z and 0-9, so all Cyrillic or Chinese assumptions looked identical). Fingerprints of assumptions containing letters outside a-z may differ from v0.2's.
- The library no longer prints; set `SHL_VERBOSE=1` to see its messages. An empty `SHL_...` variable now counts as unset.
- `shl_events` has a new layout. Old entries are carried over without their notes.

**Wording**
- The person sees "assumptions"; the technical docs say "inferences". v0.2 said "guesses".

**Also**
- `pyproject.toml`: `pip install git+https://github.com/AI-Proof/shadow-hypothesis-ledger` for use outside Hermes.
- `shl_ledger.hooks`: framework-neutral `before_model`, `after_model`, `before_tool`.
- Lifecycle simulation (`eval/simulate_lifecycle.py`, results in `eval/SIMULATION.md`).
- CI on Linux, Windows and macOS, Python 3.10 to 3.13.
- The upgrade tolerates an older SHL still running in another process (a gateway not yet restarted): the database is migrated once.
- `hermes shl` output keeps the frame on consoles that can't encode it (Windows with redirected output).
- 147 tests.

## 0.2.0 (September 2026)

First public release, under the Apache License 2.0. Builds on the technical preview (0.1) that ran on the author's Hermes profile.

**New protections**
- **Renamed guesses are refused.** A rejected guess can no longer come back under a new id with the same or near-identical wording. `stage` points to the rejected id instead.
- **`forget` erases a guess for good.** The text is deleted; only the id, a one-way hash and the change history remain. The hash still blocks the same guess from being added again.
- **Change history.** Every stage, reject, reconsent and forget is logged with time and statuses. The log never stores guess text.
- **`screen(text)`** checks text before it becomes memory or a summary, and replaces sentences that repeat a rejected guess with `[parked: ID]`. Removes API-key-like strings.
- **Ghost detection handles rewording** (word overlap with light stemming) instead of exact phrases only. When it fires, the agent is told to ask the person rather than assume.
- **Evidence tracking is opt-in** (`SHL_EVIDENCE=1`). By default a rejected guess collects nothing.
- **`review`** lists parked guesses that may deserve another look. Read-only; nothing is ever promoted without the person.

**After the first Windows soak test (same release)**
- `forget` also clears the person's rejection reasons from the log.
- `counter` now counts as evidence, so `review` can list a guess after three pieces of evidence either way.
- `SHL_GHOST_MODE=inert`: never offer to restore a parked guess.
- `SHL_GATE_NOTE=1`: add a line saying the block is a filter, not proof.
- 25 tests.

**Other**
- Proper Python package (`shl_ledger`); tests run with pytest or plain Python (25 tests).
- The database upgrades itself from the 0.1 format.
- SQLite WAL mode for safer concurrent access; connections checkpoint-and-close so Windows tests can delete the temp file.
- Tests use `TemporaryDirectory(ignore_cleanup_errors=True)` (Windows file locks).
- The injected block caps parked ids at 64.
- Leakage test harness (`eval/`) for measuring what models actually do.
