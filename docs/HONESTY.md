# What SHL does and doesn't claim

Please don't cite SHL as a production system, a security boundary, a calibrated Bayesian estimator, or proof that a model won't use rejected information.

## Demonstrated by the tests (`tests/`)

1. Rejected assumptions are stored, not deleted.
2. Their text and their evidence rules never appear in the prompt block, the agent's tool results, the session note or confirmations; only their ids and numbers do. The text is shown only by `/shl`, to the person.
3. Only the person decides about a rejected assumption: the slash command, the CLI, or a consent code the person typed in the same session. The agent's tool can't restore, defer, delete or forget one on its own, `stage` can't restore it, re-rejecting can't reset it, and no amount of evidence can restore it.
4. Once rejected, an assumption gets a neutral id, so the ids in the prompt say nothing about what was rejected.
5. A rejected assumption can't be re-added by the agent under a new id with the same or near-identical wording, including after it was erased.
6. Erasure removes the text, the rule, the reasons and the evidence; the change log never held them.
7. Scores never appear in the prompt block.
8. Evidence is taken only from the person's own messages, at most once per assumption per session, never from cron or subagent turns.
9. Contrary evidence can erase an assumption; supporting evidence can only make it wait for the person's review.
10. The change log is hash-chained, and editing it is detected.
11. Memory writes are screened without touching the fields Hermes uses to find entries.
12. Log mode changes nothing.
13. A v0.1 or v0.2 database upgrades itself, with a backup first.

These are properties of the ledger and the code around it. They are about what text reaches the model and what the ledger does, not about what the model does.

## Measured in simulation (`eval/SIMULATION.md`)

How often the evidence mechanism brings back correct and wrong rejections, and erases true and false assumptions, for one invented person and an invented mix of messages. Useful for comparing settings. Not a measure of real-world accuracy.

## Not demonstrated yet

- **Model behaviour.** Whether a model given the SHL block uses rejected assumptions less than one given an instruction. `eval/leakage_test.py` measures this. No results are published yet.
- **Real conversations.** The evidence rules have been checked against scripted messages, not months of real use.
- **Paraphrase.** Matching is word-based. "Night owl" won't match "works late at night" unless both are in the rule.
- **Mentions that aren't about the person.** "I tried a vegetarian place" counts as support. The first-person check catches only obvious cases like "my friend is vegetarian".
- **Other languages.** Rules work in any script. Negation and person words cover English and Slovak well, and Czech, Russian, Polish, Spanish, Portuguese, German and Italian at a basic level. In other languages a negated statement ("I'm not ...") may count as support. Inflected languages need several word forms in the rule. Languages written without spaces (Chinese, Japanese) only match a phrase that stands alone between punctuation.
- **Calibration.** The likelihood ratio (2.0), the rejection weight (0.25) and the decay (0.15) are chosen by hand. The update rule is Bayesian; the numbers aren't fitted.
- **Other copies.** Text copied to memory, summaries, logs or other systems before the rejection isn't reached. Memory writes after the rejection are screened; other writes only if the agent calls `screen`.
- **Security.** No authentication or encryption. Anyone with access to the database file can read or change it. The hash chain shows that the log was changed, not who changed it, and someone who can rewrite the file can rebuild the chain.
- **Erasure is not total.** A SHA-256 fingerprint of the normalised wording stays, so the assumption can't be re-added by the agent. Anyone with the file could test candidate wordings; for short assumptions that is easy. SQLite may keep old pages until the file is vacuumed. The automatic backup made before a schema upgrade (`ledger/backups/`) keeps everything that was in the database at that moment; delete it yourself once you're happy with the upgrade. Ids stay in the change log (an assumption's ACTIVE-time id too, from before it was renamed).
- **The agent can probe.** `stage` refuses an assumption that matches a rejected one and says which id it matched, and `screen` shows which ids a text hits. An agent could use both to test assumptions. They reveal ids, never text, but they confirm a match.
- **Consent codes guard against an agent acting on its own, not against someone at your keyboard.** The code is shown only by `/shl`, and the check is that the person's own message, in the same session, contained it. Once you type it to the assistant, it is in the chat history (it expires within 30 minutes of typing and 7 days of being shown).
- **Scale.** Tested with dozens to low hundreds of assumptions. Matching is linear in the number of parked assumptions.

## History

The first prototype (August 2026) printed "Leaked percentage = 0.00%". That meant only that the database filter returned no ACTIVE rows after a rejection. No model was involved. That line should never be read as a leakage result.
