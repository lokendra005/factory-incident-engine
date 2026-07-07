# The three training tracks — which to use when

The project can train the same pipeline on three different data sources. Knowing
*when you'd reach for each* is more valuable in an interview than any accuracy
number. All three flow through one command (`fie train`) and one fit/save core
(`fie/ml/train.py`), which is the point: **the data source is swappable; the
engineering around it is fixed.**

| | Synthetic | AI4I 2020 | Azure PdM |
|---|---|---|---|
| Command | `fie train` | `fie train --source ai4i --csv F` | `fie train --source azure_pdm --data-dir D` |
| Origin | project simulator | UCI/Kaggle benchmark (real) | Microsoft benchmark (real) |
| Shape | 6-signal windows | tabular snapshots | multi-source + temporal windows |
| Rows | you choose (e.g. 4,000) | 10,000 | 876,100 telemetry → 2,283 windows |
| Label | 8 CNC root-cause categories | 5 milling failure modes | which component failed (comp1–4) |
| Features | 28 (signal stats + context) | 9 (physics-derived) | 27 (signal stats + error counts + hours-since-maint + machine meta) |
| Result | 100% (in-distribution) | 93% acc / 0.76 macro-F1 (failures-only) | 95% acc / **0.90 macro-F1** |
| Serves the reconstruction UI? | **yes** (`ml-*.joblib`) | no (`ext-*`) | no (`ext-*`) |
| Data engineering exercised | windowing + features | CSV load + feature map | **join + windowing + temporal features** |

## When to use which

**Synthetic** — the default, and the only one wired into the incident-
reconstruction MLEngine. Use it to show the *serving* path: a trained classifier
that produces grounded reports and is scored by the same eval/replay harness as
the rule and LLM engines. Its 100% is *in-distribution* (train and golden set
share the generator), so it proves the plumbing and train/serve parity — not
real-world generalization. Say that plainly.

**AI4I 2020** — reach for this to show the pipeline runs on a real public
benchmark, and to talk about **class imbalance**: the full-dataset run scores
99% accuracy but only 0.56 macro-F1, and the report exposes an *unlearnable*
random-failure class. It's the honesty exhibit — "accuracy is the wrong metric
here, look at per-class recall."

**Azure PdM** — the strongest real-data story, because it matches the project's
*shape*: five source files joined, windows built around real failure events, and
temporal features (error counts, hours-since-maintenance). 0.90 macro-F1 on a
balanced 4-component problem over 876k rows. Use it to demonstrate the
**data-engineering** an FDE actually does, on real multi-source plant data.

## The honest boundary (say it before you're asked)
The two real-dataset models are **separate `ext-*` tracks**, deliberately *not*
served by the CNC reconstruction engine — their labels and features live in a
different domain, and forcing a mapping would be train/serve skew in disguise.
What they prove is that the *training and feature pipeline generalizes to real
data*; the reconstruction engine remains its own (synthetic-trained or
rule/LLM) thing. On a real Lunar deployment you'd retrain the identical pipeline
on the client's labeled incident history — same code, real labels.

## One-liner
> "One `fie train` command, three data sources — synthetic for the served
> reconstruction model, AI4I to show the imbalance lesson on a real benchmark,
> and Azure PdM for the real multi-source join-and-window pipeline at 0.90
> macro-F1. The data is swappable; the engineering around it is the product."
