# Changelog

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
