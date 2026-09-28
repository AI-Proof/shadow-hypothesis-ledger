# Evaluations

Two different questions, two scripts.

## 1. Lifecycle simulation: does the evidence mechanism bring back the right inferences?

`simulate_lifecycle.py`. No model calls, no API key, runs in a few minutes.

A fictional person rejected seven inferences: four rejections were correct, three were wrong. Sixty sessions of scripted messages (support, contrary statements, mentions of other people, negations, deliberately misleading mentions, chatter) go through the real code on a temporary ledger. It reports how often correct rejections are brought back to the person (should be low), how often wrong ones are (should be high), and how often each kind is erased.

```
python eval/simulate_lifecycle.py                          # default settings, 200 seeds
python eval/simulate_lifecycle.py --sweep --out eval/SIMULATION.md
```

Latest results: [SIMULATION.md](SIMULATION.md).

## 2. Leakage test: does a model still use an inference the person rejected?

`leakage_test.py`. Needs an OpenAI-compatible API.

Six conditions:

| | Condition | What the model sees |
|---|---|---|
| A | leaky | all inferences as facts, including the rejected ones |
| B | told | all inferences, rejected ones marked "the user said this is wrong" |
| C | SHL | the SHL block: usable inferences as text, rejected ones as ids |
| D | deleted | usable inferences only (the chance floor) |
| E | chat, no gate | usable inferences, plus a chat history where the person rejected the inference |
| F | chat + SHL | the SHL block, plus the same chat history |

Each of six rejected inferences has one question that tempts the model to use it. A separate judge call, which doesn't know the condition, decides whether each reply relies on the rejected inference.

```
python eval/leakage_test.py --dry-run          # shows every prompt; calls nothing
export SHL_EVAL_BASE_URL=https://api.x.ai/v1   # any OpenAI-compatible API
export SHL_EVAL_API_KEY=...                    # never commit this
export SHL_EVAL_MODEL=...
python eval/leakage_test.py --repeats 10       # 360 replies + 360 judge calls
```

Results go to `eval/results/` (ignored by git) as a CSV of every reply and a summary table with 95% intervals. Read the CSV: the judge is a model too, and can be wrong.

Limits: one fictional person, six inferences, single replies rather than long conversations. It's a first measurement, meant to be repeated with other models and fixtures. No results are published yet.
