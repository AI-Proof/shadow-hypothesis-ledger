# SHL specification (v0.2)

## Purpose

Keep guesses about a person that the person rejected out of an AI agent's prompt, without deleting the record of the rejection, and let only the person bring a guess back.

## Data

One SQLite file per agent profile.

`shl` table:

| Column | Meaning |
|---|---|
| `hypothesis_id` | stable id, chosen by the agent (for example `HYP_CITY`) |
| `insight_text` | the guess, in plain words; empty once erased |
| `text_hash` | SHA-256 of the normalised text; kept after erasure |
| `posterior_confidence` | a label for the person to read; never used as a weight |
| `evidence_counter` | how many times evidence was recorded against or for a parked guess |
| `last_update_ts` | Unix time of the last change |
| `status` | `ACTIVE`, `SHADOW`, `REINFORCED`, `EXPIRED` or `ERASED` |
| `rejection_metadata` | JSON with the person's reason |

`shl_events` table: time, id, action, status before, status after, and a short note (the person's reason on reject). It never stores guess text. `forget` clears the notes for that id.

## Transitions

| From | Operation | To | Who may trigger it |
|---|---|---|---|
| (none) | `stage` | ACTIVE | agent |
| ACTIVE | `stage` (same id) | ACTIVE (text updated) | agent |
| any except ERASED | `reject` | SHADOW | agent, on the person's word |
| SHADOW, REINFORCED | `corroborate` | REINFORCED | agent, only if `SHL_EVIDENCE=1` |
| SHADOW, REINFORCED | `counter` | SHADOW, or EXPIRED below 0.30 | agent, only if `SHL_EVIDENCE=1` |
| any except ERASED | `reconsent REVIEW` | ACTIVE | the person |
| any except ERASED | `reconsent DEFER` | SHADOW | the person |
| any except ERASED | `reconsent DELETE` | EXPIRED | the person |
| any | `forget` | ERASED (text removed) | the person |

Refusals:
- `stage` on an id that is not ACTIVE is refused.
- `stage` with text that is the same guess as any non-ACTIVE row (identical normalised text, identical hash, or word overlap of 0.8 or more) is refused, and the matching ids are returned.
- `reconsent` on an ERASED row is refused: the text no longer exists.

## The prompt block

Built by `inject.build_context(user_message)` before every model call:
- ACTIVE rows: id and text, up to 32 rows, 400 characters each.
- SHADOW, REINFORCED and EXPIRED rows: ids only, up to 64.
- ERASED rows: nothing.
- Scores: never.
- If the current message repeats a parked guess (see matching), a GHOST line with the ids. With `SHL_GHOST_MODE=ask` (default) the agent is told to ask whether to restore it; with `inert`, never to offer that.
- With `SHL_GATE_NOTE=1`, a first line stating the block is a filter, not proof.

## Matching

`similarity.py`, no dependencies. Text is lower-cased, accents removed, punctuation dropped, stopwords removed (but not "not" and other negations), and words lightly stemmed (plural and -ing/-ed endings).
- **Ghost / screen:** a parked guess matches a text if its normalised form appears in it, or if at least 75% of its content words do. Guesses with fewer than three content words match only exactly.
- **Same guess (re-staging guard):** identical normalised text or hash, or a word-set overlap (Jaccard) of 0.8 or more.

This catches rewording with mostly the same words. It misses paraphrase. A future version could add an optional embedding model; the default stays dependency-free and local.

## Review

`review_due()` lists SHADOW and REINFORCED rows with 3 or more pieces of evidence (corroborating or counter), or unchanged for 90 days. The list goes to the person on request. It never reaches the prompt and never promotes anything.

## Screening

`screen(text)` splits text into sentences, replaces each sentence that matches a parked guess with `[parked: ID]`, and removes strings that look like credentials. Intended for text about to be saved as memory or a summary.

## Constants

`BOOST = 0.05`, `DECAY = 0.15`, `EXPIRE_AT = 0.30`, `REVIEW_EVIDENCE = 3`, `REVIEW_DAYS = 90`, ghost threshold 0.75, same-guess threshold 0.8. All are placeholders chosen by hand, not fitted to data.
