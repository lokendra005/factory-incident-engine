# Learning Roadmap

A focused plan to go from "I built this" to "I can defend every line and extend
it live." Each day maps topics → the exact files in this repo → external
resources. Do the **Recall check** at the end of each day out loud.

> Principle: learn the concept, then immediately point to where it lives in the
> code. Interviewers trust "here's the function that does it" far more than
> textbook definitions.

---

## Day 1 — The shape of the system + data engineering core

**Topics:** ETL/ingestion, canonical models, idempotency, exactly-once.
**Code:** `fie/ingestion/pipeline.py`, `fie/ingestion/validate.py`, `fie/store.py`,
`fie/models.py`, `fie/schema.sql`.
**Read here:** `02-data-engineering/ingestion-and-etl.md`,
`idempotency-and-exactly-once.md`; `05-code-walkthrough/data-flow-trace.md`.
**Resources:**
- *Designing Data-Intensive Applications* — Kleppmann, ch. 1, 11 (idempotency,
  exactly-once, stream processing). The single best reference for this whole day.
- Confluent blog: "Exactly-Once Semantics" explainer.
**Recall check:** Explain how a duplicate telemetry line, a crash mid-file, and a
re-delivered record all end up correct. Name the functions.

## Day 2 — Fault tolerance: DLQ, checkpoints, schema drift, recovery

**Topics:** dead-letter queues, checkpoint/resume, schema evolution, "never
crash the run."
**Code:** `fie/ingestion/pipeline.py` (`recover_dlq`, checkpoint logic),
`validate.py`, `fie/store.py` (dlq/checkpoint/drift tables),
`tests/test_ingestion.py`.
**Read here:** `02-data-engineering/dead-letter-queues.md`,
`checkpoints-and-recovery.md`, `schema-drift-and-validation.md`.
**Resources:**
- AWS/Google docs on dead-letter queues (SQS DLQ, Pub/Sub dead-letter topics).
- "Schema evolution" in the Confluent Schema Registry docs.
**Recall check:** Walk through the "fix an upstream rename → replay the DLQ →
13 recovered" story and the crash-safety test.

## Day 3 — The agent: tools, grounding, the pure-function design

**Topics:** what an agent is, tool use, grounding, why the engine is pure.
**Code:** `fie/agent/tools.py`, `fie/agent/engine.py`, `fie/agent/reconstruct.py`,
`fie/models.py` (`EvidenceBundle`, `IncidentReport`, `RunTrace`).
**Read here:** `01-concepts/agents-and-tools.md`,
`groundedness-and-hallucination.md`; `05-code-walkthrough/architecture-walkthrough.md`.
**Resources:**
- ReAct paper (Yao et al., "ReAct: Synergizing Reasoning and Acting").
- Anthropic "Building effective agents" guide; Anthropic tool-use docs.
**Recall check:** Why is `reconstruct(bundle) -> report` a *pure* function, and
what two capabilities does that purity unlock?

## Day 4 — Reliability gate + evaluation harness

**Topics:** data-quality gating/abstention, offline eval, golden sets, metrics.
**Code:** `fie/reliability.py`, `fie/eval/evaluators.py`, `fie/eval/harness.py`,
`fie/eval/golden.py`, `data/golden/*.json`.
**Read here:** `02-data-engineering/data-quality-and-gating.md`,
`01-concepts/evaluation-harness.md`.
**Resources:**
- OpenAI Evals / "LLM-as-a-judge" writeups; Anthropic eval guidance.
- Great Expectations docs (data-quality checks vocabulary).
**Recall check:** Name the five things the harness scores and why groundedness is
kept separate from correctness.

## Day 5 — Replay, regression, determinism

**Topics:** trace capture, deterministic replay, regression gating (SHIP/HOLD).
**Code:** `fie/replay/replay.py`, `fie/replay/regression.py`,
`tests/test_eval_replay.py`.
**Read here:** `01-concepts/replay-and-determinism.md`,
`observability-and-tracing.md`.
**Resources:**
- "Testing machine learning systems" (Google ML Test Score paper: Breck et al.).
- Any CI/CD "regression gate" article.
**Recall check:** How does replaying a *snapshot* make a diff attributable purely
to the engine change? What makes the reverse run say HOLD?

## Day 6 — Backends: Grok/Claude/ML + the training question

**Topics:** LLM APIs, JSON output, fallback; feature extraction; training a
classifier; train/serve skew.
**Code:** `fie/agent/llm.py`, `fie/agent/features.py`, `fie/ml/dataset.py`,
`fie/ml/train.py`, `fie/agent/ml_engine.py`.
**Read here:** `07-extending/model-backends-grok-claude-ml.md`,
`training-and-datasets.md`; `01-concepts/llm-apis.md`.
**Resources:**
- xAI API docs (console.x.ai) — OpenAI-compatible chat completions.
- scikit-learn user guide (pipelines, RandomForest, train/test split).
- "Data leakage / train-serve skew" — any MLOps primer.
**Recall check:** What does "train on a large dataset" actually mean for each of
the three backends, and which one does the project literally train?

## Day 7 — Domain, delivery, and the interview

**Topics:** plant systems, RCA, the pitch, honest weaknesses.
**Code:** `fie/simulator/scenarios.py`, `fie/web/`, `fie/cli.py`, `docs/`.
**Read here:** `03-manufacturing-domain/*`, all of `06-interview-prep/`.
**Resources:**
- OPC Foundation "What is OPC-UA" overview; a MES/ERP primer (e.g. MESA model).
- Skim a vendor page (Ignition/AVEVA historian) for vocabulary.
**Recall check:** Deliver the 90-second pitch, then answer the four questions
without notes.

---

## If you only have one evening

Read `05-code-walkthrough/data-flow-trace.md` and
`06-interview-prep/the-four-questions.md`, run `make demo`, then run
`fie regression` and read the output. That covers 80% of what you'll be asked.
