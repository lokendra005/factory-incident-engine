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

---

# Concept inventory (everything this project touches)

Tick each when you can explain it in one breath and point to where it lives in
the repo. Deep dives are in the folder shown.

**AI / agents** (`01-concepts/`)
- [ ] Agent vs pipeline vs single prompt — `agents-and-tools.md`
- [ ] Tool use / function calling — `fie/agent/tools.py:Toolbox`
- [ ] Grounding / anti-hallucination (cited ids ⊆ evidence) — `groundedness-and-hallucination.md`
- [ ] LLM APIs: messages, tokens, temperature, JSON output, fallback — `llm-apis.md`, `fie/agent/llm.py`
- [ ] RAG vs structured retrieval (why SQL not vectors here) — `rag-and-retrieval.md`
- [ ] Evaluation harness, golden set, the 5 metrics — `evaluation-harness.md`
- [ ] LLM-as-judge vs rule-based judges
- [ ] Deterministic replay + regression gating — `replay-and-determinism.md`
- [ ] Observability / run traces — `observability-and-tracing.md`

**Data engineering** (`02-data-engineering/`)
- [ ] ETL/ELT, batch vs streaming — `ingestion-and-etl.md`
- [ ] Idempotency & exactly-once effect — `idempotency-and-exactly-once.md`
- [ ] Checkpointing & crash recovery — `checkpoints-and-recovery.md`
- [ ] Dead-letter queues + recovery — `dead-letter-queues.md`
- [ ] Schema drift & validation-before-construction — `schema-drift-and-validation.md`
- [ ] Data-quality scoring & the deployment gate — `data-quality-and-gating.md`
- [ ] Normalized store, SQLite↔Postgres, WAL, indexes — `databases-sqlite-postgres.md`

**ML** (`07-extending/`)
- [ ] Feature extraction & train/serve skew — `fie/agent/features.py`
- [ ] Class imbalance: macro-F1 vs accuracy — AI4I story
- [ ] RandomForest + StandardScaler + train/test split — `fie/ml/train.py`
- [ ] Multi-source temporal feature engineering — `fie/ml/azure_pdm.py`
- [ ] When to use rules vs ML vs LLM — `dataset-tracks-comparison.md`

**Manufacturing domain** (`03-manufacturing-domain/`)
- [ ] PLC/SCADA/MES/ERP/historian/OPC-UA/MQTT — `manufacturing-101.md`
- [ ] Failure physics (cooling vs sensor vs overload vs bearing vs tool wear)
- [ ] RCA methods (5-whys, fishbone, FMEA) — `incident-reconstruction.md`

**Python / tooling** (`04-python-and-tooling/`)
- [ ] pydantic v2, frozen dataclasses, typing — `python-patterns.md`
- [ ] pytest, fixtures, `importorskip` — `testing-pytest.md`
- [ ] stdlib `http.server`, argparse
- [ ] Docker, GitHub Actions, eval-as-a-CI-gate — `docker-and-ci.md`

# Interview question bank (from THIS project)

Full answers in `06-interview-prep/`. Practice saying each in ≤ 30s.

**System design**
- Walk me through the architecture. → `05-code-walkthrough/architecture-and-results.md`
- How does one record flow end to end? → `data-flow-trace.md`
- Why is the reasoning engine a *pure function*? (reproducible eval + deterministic replay)

**Data engineering**
- How do you get exactly-once from an at-least-once feed? (idempotent id + checkpoint)
- What happens to a corrupt / mis-typed record? (DLQ with a reason, never crashes)
- How do you detect and handle schema drift? (tolerate new field, reject missing, log)
- How does crash recovery work? (line checkpoint + idempotent replay)

**Agents / evaluation**
- Is this a real agent or a pipeline? (tools + captured calls; purity is deliberate)
- How do you stop hallucination? (cited ids must resolve to real records)
- How do you know a change is safe to ship? (replay captured traces → SHIP/HOLD)
- What are the five metrics and why five? (correctness alone ≠ deployable)

**ML**
- What is train/serve skew and how do you prevent it? (one shared feature extractor)
- Why is 99% accuracy misleading here? (AI4I imbalance → macro-F1 0.56)
- When would you reach for ML over rules? (many noisy signals; brittle thresholds)

**Behavioral / design**
- Why did you build this? → `origin-story.md`
- What's simulated vs real? → `weaknesses-and-honest-answers.md`
- What would you change at scale? → `07-extending/scaling-to-real-data.md`

# How to train it on more data

Three sources, one command each (see `07-extending/training-and-datasets.md`):

```bash
# 1. more synthetic data — this is the model the reconstruction UI serves
fie train --n-per-class 1000            # 8 classes -> 8,000 samples

# 2. real benchmark: AI4I 2020 (a milling machine, 10k rows)
fie train --source ai4i --csv data/dataset/ai4i/ai4i2020.csv --failures-only

# 3. real multi-source: Microsoft Azure PdM (876k telemetry rows)
fie train --source azure_pdm --data-dir data/dataset/azure_pdm/
```

**To add your OWN dataset** (3 edits):
1. Write a loader that returns `(X, y, feature_names)` in `fie/ml/external.py`
   (CSV) or a new module (multi-file, like `fie/ml/azure_pdm.py`).
2. Register it: add a branch in `fie/ml/train.py:train_external`.
3. Expose it: add the name to `--source` choices in `fie/cli.py`.
Artifacts land in `data/models/` (`ml-*.joblib` = served synthetic; `ext-*.joblib`
= real-dataset tracks, deliberately not served to avoid train/serve skew).

# How to ingest new / real data — and where

The pipeline consumes raw JSONL and normalizes it. Ingestion internals:
**parse → validate → dedupe (idempotent id) → DLQ bad rows → log drift →
checkpoint** (`fie/ingestion/pipeline.py:ingest_file`).

**Option A — quickest:** emit raw records in the expected shape, then ingest.
```bash
# one line per reading in data/raw/telemetry.jsonl (also maintenance.jsonl, mes.jsonl):
# {"kind":"telemetry","machine":"CNC-17","ts":"2026-07-01T14:03:00+00:00",
#  "signal":"spindle_temp_c","value":55.0}
fie ingest                 # dedup/DLQ/checkpoint handled automatically
fie recover-dlq            # re-drive dead letters after fixing an upstream issue
```
The producer that writes these today is
`fie/simulator/generate.py:write_raw_feed` — **replace this with a real connector**
(OPC-UA / MQTT / CSV export) that emits the same raw records.

**Option B — new signals / real source:**
1. Add your signals + physical bounds to `fie/config.py` (`SIGNAL_BOUNDS`, `NOMINAL`)
   — validation uses these to accept/reject values.
2. Adjust validation/normalization in `fie/ingestion/validate.py` if the record
   shape differs.
3. Ingest, then reconstruct a window:
   `fie reconstruct --asset CNC-17 --start ... --end ...`.

# Which code file does what (map)

| File | Responsibility |
|---|---|
| `fie/config.py` | all tunable constants: signals, bounds, thresholds, model names |
| `fie/models.py` | canonical pydantic models (the contract every layer shares) |
| `fie/schema.sql` / `fie/store.py` | SQLite DDL + idempotent store (upserts, DLQ, checkpoints, drift) |
| `fie/simulator/scenarios.py` | the 8 failure archetypes + labels (ground truth) |
| `fie/simulator/generate.py` | deterministic bundle builder, messy-feed writer, training variants |
| `fie/ingestion/validate.py` | raw dict → canonical model, or a DLQ reason |
| `fie/ingestion/pipeline.py` | read→validate→dedupe→DLQ→drift→checkpoint; `recover_dlq` |
| `fie/reliability.py` | data-quality score + the deployment **gate** |
| `fie/agent/tools.py` | Toolbox: query_telemetry / search_maintenance / find_similar |
| `fie/agent/engine.py` | rule engine (v1.1 bug, v1.2 fix) + shared `build_timeline` |
| `fie/agent/features.py` | shared feature extractor (train == serve) |
| `fie/agent/llm.py` | LLM base + Claude + Grok backends |
| `fie/agent/ml_engine.py` | trained-classifier engine (same report contract) |
| `fie/agent/reconstruct.py` | orchestrator: bundle → gate → engine → RunTrace → persist |
| `fie/ml/dataset.py` / `train.py` | synthetic dataset gen + train/save (+ external track) |
| `fie/ml/external.py` / `azure_pdm.py` | AI4I loader / Azure PdM multi-source loader |
| `fie/eval/*` | golden set + evaluators + harness |
| `fie/replay/*` | deterministic replay + regression report |
| `fie/web/*` | control-room UI (server, charts, templates, actions) |
| `fie/cli.py` | every command (demo, ingest, reconstruct, eval, regression, train, serve) |
