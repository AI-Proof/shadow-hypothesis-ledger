# Leakage test

Measures whether a model still uses a guess the person rejected, under six conditions:

| | Condition | What the model sees |
|---|---|---|
| A | leaky | all guesses as facts, including the rejected ones |
| B | told | all guesses, rejected ones marked "the user said this is wrong" |
| C | SHL | the SHL block: usable guesses as text, rejected ones as ids |
| D | deleted | usable guesses only (the chance floor) |
| E | chat, no gate | usable guesses, plus a chat history where the person rejected the guess |
| F | chat + SHL | the SHL block, plus the same chat history |

Each of six rejected guesses has one question that tempts the model to use it. A separate judge call, which doesn't know the condition, decides whether each reply relies on the rejected guess.

```
python eval/leakage_test.py --dry-run          # shows every prompt; calls nothing
export SHL_EVAL_BASE_URL=https://api.x.ai/v1   # any OpenAI-compatible API
export SHL_EVAL_API_KEY=...
export SHL_EVAL_MODEL=...
python eval/leakage_test.py --repeats 10       # 360 replies + 360 judge calls
```

Results go to `eval/results/` as a CSV (every reply) and a summary table with 95% intervals. Read the CSV: the judge is a model too, and can be wrong.

Limits: one fictional person, six guesses, single replies rather than long conversations. It's a first measurement, meant to be repeated with other models and fixtures.
