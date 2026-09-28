# Contributing

Issues and pull requests are welcome. Most useful right now:

- **Leakage results.** Run `eval/leakage_test.py` against a model you have access to and open an issue with the summary table (not your API key). Results from different models are the missing evidence.
- **Evidence rules in other languages.** Negation and first-person words live in `shl_ledger/rules.py`. Additions for your language, with a test in `tests/test_shl.py`, are very welcome.
- **Real-world misfires.** If SHL brought back an assumption it shouldn't have, or missed one, describe the sentence that caused it (made up or anonymised, never someone's real data).
- **Other agent frameworks.** `shl_ledger/hooks.py` is framework-neutral. An adapter for another agent is a good first contribution.

Ground rules:

1. Keep the invariants in [docs/SPEC.md](docs/SPEC.md#invariants). A change that lets parked text reach a prompt or a tool result, or lets anything but the person restore an assumption, won't be merged.
2. No new runtime dependencies. The ledger stays standard-library Python.
3. `python -m pytest -q` must pass. Add a test for what you change.
4. Never commit a real ledger (`*.sqlite`), API keys, or anyone's personal data. Test fixtures use invented people.

By contributing you agree that your contribution is licensed under the Apache License 2.0, including its patent grant.
