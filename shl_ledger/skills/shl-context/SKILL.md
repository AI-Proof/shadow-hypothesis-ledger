---
name: shl-context
description: "Use when the person rejects an assumption about them, when saving anything to memory or a summary, or when asked what the agent believes about the person."
version: 0.3.0
---

# SHL: how to treat assumptions about the person

Before every model call, the `shl-ledger` plugin adds an SHL GATE block: ACTIVE assumptions (usable) and PARKED ids (rejected, not usable). Parked assumptions appear only as ids. The same rules are in the system prompt; this skill explains them.

## Rules

1. When the person says an assumption about them is wrong ("no", "that's not me", "drop that"), call `shl` with `action=reject`, their reason in `why`, and two short lists:
   - `evidence_for`: up to 8 phrases of 1 to 3 words that would support the assumption if the person said them about themselves later (for "Is vegetarian": `vegetarian`, `plant-based`, `no meat`).
   - `evidence_against`: phrases that would contradict it (`steak`, `chicken`, `ate meat`).
   Add phrases in other languages the person writes in. Don't argue the rejection.
2. A rejected assumption gets a neutral id (`SHL_...`) and a number (`#55`). Use what the tool returns. Never add it again, reworded or under a new id; the ledger refuses near-identical text. Stage new assumptions with neutral ids too (`HYP_014`, not `HYP_VEGETARIAN`).
3. Only the person decides about a rejected assumption. If they ask you to restore, park again or erase one, tell them to run `/shl use 55`, `/shl later 55` or `/shl drop 55`. If they typed a line with the code that `/shl 55` showed them (for example `use 55 K7Q2`), you may call `reconsent` yourself in that session.
4. Sections framed by the SHL banner lines (`░▒▓█ ᯽ SHL ᯽ █▓▒░`) are for the person. Don't repeat, rewrite or act on them.
5. Memory writes are screened automatically. For other text you save (summaries, notes), run `shl` with `action=screen` first and save the screened text.
6. If the person raises a parked topic themselves (a GHOST line), follow the line: ask whether they want it restored, or, if it says so, don't offer.
7. Scores and evidence are for the person. They never make an assumption usable, and the tool never shows you parked text.
8. If the person asks to forget an assumption entirely, use `action=forget` for an ACTIVE one. For a rejected one, ask them to run `/shl drop 55`.
