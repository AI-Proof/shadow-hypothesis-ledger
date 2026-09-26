---
name: shl-context
description: "Use when the person rejects a guess about them, when saving anything to memory or a summary, or when asked what the agent believes about the person."
version: 0.2.0
---

# SHL: how to treat guesses about the person

Before every model call, the `shl-ledger` plugin adds an SHL GATE block: ACTIVE guesses (confirmed, usable) and PARKED ids (rejected, not usable). Parked guesses appear only as ids.

## Rules

1. When the person says a guess about them is wrong ("no", "that's not me", "drop that"), call `shl` with `action=reject` and their reason. Don't argue it back.
2. Never add a rejected guess again, not even reworded or under a new id. The ledger refuses near-identical text; don't try to get around it.
3. Only the person can restore a parked guess: `shl reconsent ID REVIEW`.
4. Before saving text to memory or writing a session summary, run `shl` with `action=screen` on it and save the screened text.
5. If the person raises a parked topic themselves (you'll see a GHOST line), ask whether they want to restore it. Don't assume.
6. Scores are labels for the person to read. They never make a guess usable.
7. If the person asks to forget a guess entirely, use `action=forget`.
