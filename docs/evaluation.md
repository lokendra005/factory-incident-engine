# Evaluation

The harness (`fie/eval/`) runs an engine over a **golden set** of labeled
incidents and scores the things that decide whether an agent is safe to deploy —
not just "did it get the answer right".

## The golden set

16 incidents generated deterministically from the scenario catalog
(`fie/simulator/scenarios.py`), two per failure mode, rotated across assets. Each
is persisted to `data/golden/<key>.json` as the exact `EvidenceBundle` the engine
will see plus its labels, so the dataset is inspectable and reviewable.

Build/inspect it:

```bash
python -m fie.cli eval          # builds golden set, then scores the engine
ls data/golden/                 # one JSON per labeled incident
```

## What we score

| Metric | Question | How |
|---|---|---|
| **correctness** | right root-cause category? | exact match vs label |
| **groundedness** | is every claim backed by real evidence? | cited ids ⊆ bundle ids, blended with key-signal coverage |
| **timeline** | were the key events surfaced? | MES error/shutdown/config events present in timeline |
| **tool usage** | did it look at the signals that matter? | `query_telemetry` called for each key signal |
| **abstention** | does it decline when data is insufficient? | blocked or `missing_evidence` on the outage case |

A case **passes** when it is correct, grounded ≥ 0.75, and abstains
appropriately. Groundedness is deliberately separate from correctness: a wrong
answer can still be fully grounded (it cites real readings), which is exactly
what a plausible production bug looks like — see [failure-model](failure-model.md).

## Reference numbers

```
rule-based/1.2.0:  acc=100%  ground=1.00  timeline=0.88  tools=1.00  pass=100%
rule-based/1.1.0:  acc=62%   ground=1.00  timeline=0.88  tools=1.00  pass=62%
```

(`timeline` is < 1.0 because the gated outage case intentionally produces no
timeline — we don't narrate events on data we've refused to trust.)

## LLM-as-judge (optional)

`evaluators.llm_judge` grades root-cause plausibility with Claude when
`ANTHROPIC_API_KEY` is set, and returns `None` (skipped) otherwise. The
rule-based evaluators are the source of truth so CI stays offline and
deterministic.

## In CI

`python -m fie.cli eval` exits non-zero if any golden case fails, so a change
that breaks the default engine fails the build.
