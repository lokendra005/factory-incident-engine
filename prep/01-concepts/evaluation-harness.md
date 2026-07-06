# Evaluation Harness (offline eval, golden sets, metrics, and gating CI)

## What it is

An **evaluation harness** is a test suite for an AI system: you run the system
over a fixed set of labeled examples (a **golden set** / **eval set**) and score
its outputs against known-correct answers using a set of **metrics**. It is the
AI analogue of unit/integration tests — except the thing under test is
probabilistic and judged on quality, not just pass/fail.

Key ideas:

- **Offline eval**: run against a stored dataset, no live traffic, reproducible.
- **Golden / labeled dataset**: curated inputs with expected outputs (labels).
- **Metrics**: quantify different quality dimensions (accuracy, groundedness,
  etc.). A single "accuracy" number is almost never enough for an agent.
- **Judges**: how you *decide* if an output is good. Two families:
  - **Rule-based / programmatic judge** — deterministic code checks (exact match,
    set membership, thresholds). Cheap, fast, reproducible.
  - **LLM-as-judge** — a model grades the output ("is this diagnosis
    plausible?"). Flexible for fuzzy quality, but nondeterministic, costly, and
    itself fallible.
- **Pass criteria**: the rule that turns metric values into a per-case
  pass/fail.
- **Eval gate in CI**: the build fails if the eval regresses, so quality is
  enforced automatically, like tests.

## Why it matters

You cannot ship an AI system you can't measure. "It looked good in the demo" is
how agents blow up in production. For an FDE, the eval harness is often the *first
deliverable* — it's how you turn a vibes-based prototype into something a customer
can trust and iterate on safely. The interview will probe: what do you measure,
how do you avoid gaming a single metric, when do you use an LLM judge, and how do
you wire eval into CI so quality can't silently rot. This project has a compact,
opinionated answer to all four.

## How THIS project uses it

Everything lives in `fie/eval/`. The docstrings and `docs/evaluation.md` are the
authoritative description; here's the shape.

### The golden set

`fie/eval/golden.py` builds and loads the dataset. `build_golden` iterates the
scenario catalog (`fie/simulator/SCENARIOS`), calls `build_bundle(sc)` to produce
`(EvidenceBundle, labels)`, and persists each to `data/golden/<key>.json` as the
**exact bundle the engine will see plus its labels**. `load_golden` reads them
back (or builds them in-memory if none on disk). Per `docs/evaluation.md`: 16
incidents, two per failure mode, rotated across assets.

Why persist to JSON: the dataset is *inspectable and reviewable* — "show me the
eval set" is `ls data/golden/`. That's an FDE virtue; a customer can audit the
labels.

The labels carry (see usage in the evaluators): `expected_category`,
`expected_root_cause_kw`, `key_signals`, `expects_missing_evidence`, plus `key`
and `asset`.

### The metrics (rule-based judges)

Each is a pure function in `fie/eval/evaluators.py`:

- **correctness** (`correctness`): `report.root_cause_category ==
  labels["expected_category"]`. Exact category match.
- **root-cause keywords** (`root_cause_keywords`): does the free-text root cause
  mention an expected keyword. A softer content check.
- **groundedness** (`groundedness`): fraction of cited ids that resolve to real
  bundle records, blended 50/50 with key-signal coverage. Citing an id not in the
  bundle is penalized hard; if nothing is cited, that's only OK for
  `no_incident`/`unknown`. (Full detail in
  `groundedness-and-hallucination.md`.)
- **timeline accuracy** (`timeline_accuracy`): fraction of key MES events
  (`error_code`, `shutdown`, `config_change`) that appear in the report timeline.
- **tool usage** (`tool_usage`): fraction of `labels["key_signals"]` that the
  agent actually queried via `query_telemetry` — read straight off the captured
  `trace.tool_calls`. This catches an engine that guessed right without looking
  at the signals that matter.
- **abstention** (`abstention_ok`): on cases flagged `expects_missing_evidence`,
  the report must be `blocked` or list `missing_evidence` — i.e. it must *decline*
  rather than guess.

### The harness and pass criteria

`fie/eval/harness.py:evaluate` runs the engine over every golden case (via
`reconstruct(bundle, engine=engine, save=False)`), computes all metrics, and
builds an `EvalReport` (aggregate accuracy, groundedness_mean, timeline_mean,
tool_usage_mean, pass_rate) plus per-case `CaseResult`s.

The pass rule is explicit:

```python
PASS_GROUNDEDNESS = 0.75
passed = bool(correct and g >= PASS_GROUNDEDNESS and abst)
```

A case passes only if it's **correct AND grounded ≥ 0.75 AND abstains
appropriately**. Note what's deliberately *not* in the pass gate: timeline and
tool-usage are reported/observed but don't fail a case on their own — they're
diagnostic. The load-bearing bar is: right answer, backed by real evidence, and
honest about uncertainty.

### LLM-as-judge (optional, not the source of truth)

`evaluators.llm_judge` will ask Claude to grade root-cause *plausibility*
(returns `{"plausible": bool, "reason": str}`) — but only when Claude is
available; otherwise it returns `None` and is skipped. Per `docs/evaluation.md`:
"The rule-based evaluators are the source of truth so CI stays offline and
deterministic." This is the right hierarchy — deterministic judges gate; the LLM
judge is an optional richer opinion.

### Reference numbers

From `docs/evaluation.md`:

```
rule-based/1.2.0:  acc=100%  ground=1.00  timeline=0.88  tools=1.00  pass=100%
rule-based/1.1.0:  acc=62%   ground=1.00  timeline=0.88  tools=1.00  pass=62%
```

Two things to notice and be ready to explain: (1) groundedness is 1.00 for the
*buggy* engine too — a wrong answer can be fully grounded (it cites real
readings); (2) timeline is 0.88 not 1.0 because the gated outage case
intentionally produces no timeline (we don't narrate events on data we refused to
trust).

### Eval gates CI

Per `docs/evaluation.md`: `python -m fie.cli eval` exits non-zero if any golden
case fails, so a change that breaks the default engine fails the build. CI runs
it on Python 3.11 and 3.12. That's the whole point — quality is enforced like a
test, not checked by hand.

## Deeper mental model

Think of eval as three orthogonal choices:

1. **Dataset** — what examples, how labeled, how curated. Golden sets should
   cover the *confusable* cases (this one is built around easy-to-confuse pairs
   like sensor_fault vs cooling_degradation — see `docs/failure-model.md`), not
   just easy wins. Coverage of failure modes > raw count.
2. **Metrics** — measure *distinct* qualities so one number can't be gamed.
   Correctness alone would miss a right-for-the-wrong-reason answer; that's why
   groundedness and tool-usage exist as separate axes.
3. **Judge + gate** — how a metric becomes a verdict. Prefer deterministic
   judges for the gate; use LLM judges for exploratory/fuzzy signal only.

The subtle, senior insight this project encodes: **keep correctness and
groundedness independent.** If you folded grounding into correctness you'd hide
the most dangerous failure — a *confidently wrong but well-cited* answer, which is
exactly what a realistic production bug looks like. Separating the axes is what
lets the harness say "v1.1.0 is grounded 1.00 but only 62% accurate," which is a
precise, actionable diagnosis.

## Common interview questions with strong answers

**Q: Why not just measure accuracy?**
Accuracy hides *why* an answer is right or wrong. An agent can be accurate by
luck (never looked at the data — caught by tool-usage), or wrong-but-plausible
(caught by separating correctness from groundedness), or reckless on bad data
(caught by abstention). Multiple orthogonal metrics turn "it's wrong" into "it's
wrong *because* it blamed cooling without checking coolant flow."

**Q: Rule-based judge vs LLM-as-judge — when each?**
Rule-based when the correctness criterion is expressible in code (category match,
id-set membership, thresholds) — it's deterministic, free, and CI-safe; make it
the gate. LLM-as-judge for fuzzy qualities that resist a rule (tone, plausibility
of free-text reasoning) — but treat it as advisory, pin temperature, and never
let a nondeterministic judge gate the build. This project does exactly that:
rule-based judges gate; `llm_judge` is optional and skipped offline.

**Q: What's a golden set and how do you build a good one?**
A curated set of labeled inputs with expected outputs. A *good* one covers the
failure modes and the confusable boundaries, is inspectable/reviewable, and is
generated deterministically so it's reproducible. Here: 16 cases, 2 per mode,
persisted as JSON, generated from a scenario catalog (`golden.py`).

**Q: How do you stop metrics from being gamed?**
Make them measure independent things and include *process* metrics, not just
outcome. Tool-usage grades whether the agent looked at the right signals, so you
can't score well by guessing. Groundedness grades whether claims resolve to real
records, so you can't score well by fabricating citations.

**Q: How does eval fit into CI?**
`fie eval` exits non-zero on any failing case, run in the CI matrix (3.11/3.12).
So a change that regresses the default engine fails the build exactly like a unit
test would. Eval-as-gate is what makes iterative AI development safe.

**Q: The buggy engine scores groundedness 1.00 — isn't that a broken metric?**
No, it's the metric working. Groundedness measures "are your citations real,"
not "is your conclusion right." A bug that cites real temperature readings but
draws the wrong conclusion *should* be grounded-but-incorrect — and the harness
reports precisely that split (`acc=62%, ground=1.00`). Conflating the two would
hide the most realistic class of bug.

## Resources to learn more

- **OpenAI Evals** (GitHub) — an open framework for exactly this: datasets +
  graders + runners.
- **Anthropic docs: "Create strong empirical evaluations"** — how to design eval
  sets and metrics for LLM systems.
- **"G-Eval" / LLM-as-judge literature** (e.g. Liu et al., 2023; the
  Zheng et al. "Judging LLM-as-a-Judge" / MT-Bench paper) — capabilities and
  pitfalls of model-based grading.
- **HELM (Stanford) and the "Holistic Evaluation" line of work** — multi-metric
  evaluation philosophy (why one number isn't enough).
- **Ragas** — an open library of RAG-specific metrics (faithfulness, answer
  relevance) if you add retrieval-quality scoring.
