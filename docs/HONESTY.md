# What SHL does and doesn't claim

Please don't cite SHL as a production system, a security boundary, a calibrated Bayesian estimator, or proof that a model won't use rejected information.

## Demonstrated (tests in `tests/`)

1. Rejected guesses are stored, not deleted.
2. Their text never appears in the prompt block; only their ids do.
3. Only the person's `reconsent REVIEW` returns a guess to ACTIVE. `stage` can't, and evidence can't.
4. A rejected guess can't be re-added under a new id with the same or near-identical wording.
5. `forget` removes the text from the table and clears the rejection reasons for that id from the log.
6. Scores never appear in the prompt block.
7. Ghost detection and screening catch reworded sentences that share most content words.

These are properties of the ledger and the block it builds. They are about what text reaches the model, not what the model does.

## Not demonstrated yet

- **Model behaviour.** Whether a model given the SHL block uses rejected guesses less than one given an instruction. `eval/leakage_test.py` measures this. No results are published yet.
- **Paraphrase.** Matching is word-based. "Night owl" won't match "works late at night".
- **Other copies.** Text copied to memory, summaries, logs or other systems before the rejection isn't reached by SHL. `screen()` helps only if the agent calls it.
- **Security.** SHL has no authentication, encryption or tamper-proofing. An agent (or anyone) with access to the database file can change it.
- **Erasure is not total.** `forget` keeps a SHA-256 hash of the normalised wording so the guess can't be re-added. Anyone with the database file could test candidate wordings against it; for short guesses ("is vegetarian") that is easy. Treat erased guesses as hidden, not unrecoverable. SQLite may also keep old pages on disk until the file is vacuumed.
- **Scale.** Tested with dozens to low hundreds of rows. Matching is linear in the number of parked rows.

## History

The first prototype (August 2026) printed "Leaked percentage = 0.00%". That meant only that the database filter returned no ACTIVE rows after a rejection. No model was involved. That line should never be read as a leakage result.
