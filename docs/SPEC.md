# SHL specification (v0.3)

## Purpose

Keep inferences about a person (shown to the person as "assumptions") that the person rejected out of an AI agent's prompt, without deleting the record of the rejection. Let the person's later messages raise or lower the confidence of a rejected inference, by plain code and without model calls. Bring an inference back to the person's attention when the evidence warrants it, and let only the person restore it.

## Invariants

1. The text of a parked inference, and its evidence rule, never enter the prompt block, any result of the agent's tool, or anything SHL adds to the conversation (session note, confirmations). They appear only in `/shl` output, which goes to the person.
2. No evidence, score or timer makes an inference ACTIVE. Only the person's `use` (REVIEW) does.
3. Scoring a message calls no model.
4. The change log never holds inference text or reasons.

## Data

One SQLite file per agent profile: `$SHL_LEDGER_PATH`, else `$HERMES_HOME/ledger/shl.sqlite`, else `./ledger/shl.sqlite`. Before a schema upgrade the file is copied to `backups/shl-before-v3-from-vN-<time>.sqlite` next to it.

### `shl`: one row per inference

| Column | Meaning |
|---|---|
| `hypothesis_id` | id chosen by the agent while ACTIVE (1-64 of `A-Z a-z 0-9 _ . : -`); replaced by a neutral id such as `SHL_7Q2KX9` when the inference is rejected (`neutral_ids`, default on) |
| `insight_text` | the inference in plain words; empty once erased |
| `text_hash` | SHA-256 of the normalised text; kept after erasure (fingerprint) |
| `posterior_confidence` | current confidence, 0 to 0.99 |
| `evidence_counter` | supporting mentions since the rejection (or since the last DEFER) |
| `counter_evidence` | contrary mentions since the rejection |
| `status` | `ACTIVE`, `SHADOW`, `REINFORCED`, `EXPIRED` (v0.2 rows only) or `ERASED` |
| `rejection_metadata` | JSON: the person's reason, the agent's source note, session id, turn id, confidence after the no |
| `conf_at_reject` | confidence just before the rejection |
| `rejected_ts` | when it was rejected |
| `rule_json`, `rule_source` | the evidence rule and where it came from (`agent`, `llm`, `auto`, `person`) |
| `last_hit_session` | the session that last produced evidence (one hit per session) |
| `next_review_ts` | when the timed review falls due |
| `num` | the short number the person uses (`#55`); set while parked |
| `sim_confidence`, `sim_counter`, `sim_last_hit_session` | the log-mode shadow of the confidence, support count and session |
| `last_update_ts`, `created_ts` | times |

### `shl_evidence`: one row per piece of evidence

Time, id, direction (`for` or `against`), the phrase that matched, an excerpt of the person's sentence (up to 120 characters), session id, confidence before and after, whether it was applied (0 in log mode), and a note (`would_notice`, `would_erase` in log mode). Deleted when its inference is erased or restored.

### `shl_notices`: what waits for the person, at most one per inference

Reason (`evidence` or `timed`), created, last viewed, consent code, state, first mentioned in a session note. Deleted when the person answers.

### `shl_sessions`

Sessions that already got their session note (the latest 500).

### `shl_events`: the change log

Append-only. Each row: sequence number, time, id, action, status before and after, confidence before and after, a short technical note (never text or reasons), the previous row's hash and its own hash: `sha256(prev_hash + json([ts, id, action, from, to, from_conf, to_conf, note]))`. `verify_log()` recomputes the chain.

## Transitions

| From | Operation | To | Who |
|---|---|---|---|
| (none) | `stage` | ACTIVE | agent or person |
| ACTIVE | `stage` (same id) | ACTIVE (text updated) | agent or person |
| ACTIVE | `reject` | SHADOW (renamed to a neutral id), or ERASED if confidence < `shadow_threshold` | agent, on the person's word |
| parked | `reject` | no change (re-rejecting never resets evidence or cancels a review) | anyone |
| SHADOW, REINFORCED | evidence for | REINFORCED | the person's own message (plain code) |
| SHADOW, REINFORCED | evidence against | same, or ERASED below `expire_at` | the person's own message |
| parked | `reconsent REVIEW` | ACTIVE, confidence back to its pre-rejection value, reasons dropped | the person: slash command, CLI, or the agent's tool with a typed consent code |
| parked, ACTIVE | `reconsent DEFER` | SHADOW, evidence counter reset, review moved `timed_review_days` out | the person (the agent only with a code) |
| parked | `reconsent DELETE` | ERASED (or row deleted with `purge_mode=full`) | the person (the agent only with a code) |
| ACTIVE | `forget` | ERASED; the fingerprint is always kept | agent or person |
| parked | `forget` | ERASED | the person only (the agent's tool refuses) |

Refusals:
- `stage` on an id that is not ACTIVE.
- `stage` of text that is the same inference as a non-ACTIVE row: identical normalised text, identical hash, or word-set overlap (Jaccard) of 0.8 or more. Returns the matching ids. Exception: the person (`by="person"`) may re-add an inference whose only match is ERASED.
- `reconsent` on an ERASED row: the text no longer exists.

## Evidence rules

Stored per parked inference:

```json
{"for": ["vegetarian", "plant-based", "no meat"], "against": ["steak", "chicken"], "first_person": true, "source": "agent"}
```

At most 20 phrases per list, 60 characters each. Source order is set by `rule_source` (default `agent,auto`):
- `agent`: `evidence_for` / `evidence_against` given in the `reject` call;
- `llm`: one host-provided model call at rejection (Hermes: `ctx.llm.complete`), parsed from JSON; any failure falls back;
- `auto`: the inference's own content words, one phrase for the whole inference and one per clause (split at commas, "and", "but", "or"), with framing words such as "prefers", "wants", "likes" dropped; "against" comes from negation.
The person can replace a rule (`/shl rule ID for ... against ...`, or `auto`).

## Matching (`rules.check`)

Per sentence of the person's message (sentences split on `.!?;` and newlines):
1. Normalise: case-folded, accents removed, punctuation dropped, letters of any script kept; English stopwords dropped except negations; light English stemming (`-s`, `-ed`, `-ing`, `-ies`). Text with no letters or digits is fingerprinted from its raw form.
2. With `first_person`, skip a sentence that has no first-person word (`i`, `me`, `we`, Slovak `ja`, `som`, Czech `jsem`, Russian `я`, Spanish `yo`, `soy`, Portuguese `eu`, `sou`, ...) and does have a word for someone else (`he`, `she`, `friend`, `wife`, `sister`, and the same in the languages above).
3. A phrase of up to 3 content words must appear in order, with at most 2 other words between neighbours. A longer phrase matches if 75% of its content words appear.
4. A negation within 4 words before the match (`not`, `no`, `never`, `don't`, `no longer`, `without`, `stopped`; Slovak and Czech `nie`, `ne`, `nikdy`, `nejsem`; Russian `не`, `нет`, `никогда`; Spanish and Portuguese `no`, `nunca`, `não`; German `nicht`, `kein`; Italian `non`, `mai`; ...) turns a "for" match into "against", and cancels an "against" match.
5. One result per message. If a message holds both, "against" wins.

## Scoring (`scoring.py`)

With `p` the current confidence:

| Event | Update | Default |
|---|---|---|
| rejection | `p = p*w / (p*w + 1 - p)` | `rejection_weight` w = 0.25 |
| evidence for | `p = min(0.99, p*L / (p*L + 1 - p))`, support +1 | `likelihood_ratio` L = 2.0 |
| evidence against | `p = max(0, p - d)`, contrary +1 | `decay` d = 0.15 |
| `p < expire_at` | erase | 0.30 |

At most one piece of evidence per inference per session (`session_id`, or the calendar day when there is none). Cron and subagent turns are skipped. With `rejection_weight = 1` the numbers are those of the author's original specification (confidence kept at its pre-rejection value).

## Reviews: what waits for the person

A parked inference starts waiting for the person's decision (a row in `shl_notices`, with a 4-character consent code) when:
- support ≥ `review_evidence` (3) and confidence ≥ `notice_threshold` (0.65), or
- `timed_review_days` (90) > 0 and `next_review_ts` has passed.

Waiting is only evaluated in `evidence_mode=on`. Nothing about a waiting inference changes until the person decides.

### Numbers

Each parked inference gets a short number (`num`, shown as `#55`), assigned at rejection (or on DEFER of an ACTIVE row, or when an older ledger is upgraded), monotonic and never reused (`shl_meta.next_num`). Every command and the agent's tool accept a number, `#number` or an id. A bare number is only ever read as a number (ids may not be all digits), and `use` and `drop` act only on parked inferences.

### The session note

`session_summary(session_key)` runs once per session (`shl_sessions` remembers greeted sessions; the key is the host's session id, or the calendar day) and returns counts only: newly waiting because of evidence, newly waiting because of the timed check, still waiting from before (and how long), purged since the last note, parked in total, ever rejected, trial-mode would-notice / would-erase counts, parked inferences without a rule or with an old descriptive id, an upgrade marker, and whether the change log verifies. `notices.session_note()` renders it; `hooks.after_model()` puts it before the first reply of the session. It never contains inference text or consent codes. "Since your last session" means since the last note shown in any session: one window for the whole ledger.

### Confirmations

When the agent's tool parks an inference, erases a weak one at rejection, is refused a decision it tried to make without the person, carries out a decision the person typed, or tries to stage a rejected inference again, a one-line confirmation is queued for the session and appended to the end of that turn's reply. Rejections in one turn are merged into one line.

### Frame

Everything SHL shows the person (session note, confirmations, `/shl` output) is wrapped as `░▒▓█ ᯽ SHL ᯽ █▓▒░`, an empty line, the body, an empty line, `░▒▓███• ❉ •███▓▒░`. Both lines are 17 characters; the bottom one is a palindrome.

## Consent

`reconsent(..., by="agent")` accepts a decision only if one of the person's recent messages in the same session, typed within the last 30 minutes, contains `<VERB> <REF> <CODE>`, where VERB is `use`/`later`/`drop` (or `REVIEW`/`DEFER`/`DELETE`), REF is the inference's number, `#number` or id, and CODE is its current code; case and spacing are ignored. The code is valid for 7 days after the waiting inference was created or last viewed with `/shl`. It is shown only by `/shl <number>`, which goes to the person and never into the model's history. The messages are recorded in memory by `hooks.before_model`, which sees only the person's messages. `by="person"` is used by the slash command, the CLI, and library callers acting for the person. The agent's tool also refuses `forget` on a rejected inference, and its `history` shows only events filed under ids that still exist (an id from before a rename may describe the inference).

## Evidence modes

- `on`: as above.
- `log`: evidence is matched and recorded with `applied=0`; the would-be confidence, count and session are tracked in the `sim_` columns (so switching to `on` later isn't affected); `would_notice` and `would_erase` are noted; nothing changes status, nothing is erased, nothing starts waiting. The session note reports what would have happened.
- `off`: messages are not checked; manual `corroborate` / `counter` are refused.

## The prompt block

Built by `inject.build_context(user_message, ghosts, rules_in_system)`:
- the fixed rules (unless the host put them in the system prompt),
- ACTIVE rows: id and text, up to 32 rows, 400 characters each,
- parked rows (SHADOW, REINFORCED, EXPIRED): ids only, up to 64,
- a GHOST line when the message repeats a parked inference (near-identical wording, or a "for" phrase of its rule); wording depends on `ghost_mode`,
- with `gate_note`, a line saying the block is a filter, not proof.
Never: parked text, rules, scores, evidence.

## Screening

`screen(text)` replaces each sentence that repeats a parked inference (near-identical wording) with `[parked: ID]` and removes credential-like strings. `screen_args(args)` applies it to the values of `content`, `new_text`, `text`, `note(s)`, `summary`, `body`, `value` and `entry` inside a tool call's arguments, including nested lists (so `old_text`, which must match an existing memory entry exactly, is untouched). The Hermes `pre_tool_call` hook applies it to the tools named in `memory_tools` (default `memory`).

## Settings

Environment variable `SHL_<NAME>` first (an empty variable counts as unset), then the host's plugin settings, then the default. Invalid values fall back to the default. v0.2's `SHL_EVIDENCE=1/0` maps to `evidence_mode=on/off` (`log` also works). The ledger prints nothing unless `SHL_VERBOSE` is set. SQLite waits at most 5 seconds for a lock (Hermes stops a hook after 30). Writing connections take the write lock up front so the log's chain can't fork; read-only ones don't lock. The optional model call that writes a rule runs outside any transaction. See `shl_ledger/settings.py` for the full list with ranges.
