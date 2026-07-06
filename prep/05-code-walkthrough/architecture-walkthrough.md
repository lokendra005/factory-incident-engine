# Architecture Walkthrough

A guided tour of every module, in the order data flows through it. Read this
with the repo open. File references are `path:function`.

## The one-sentence model

`simulate → ingest → store → gate → reconstruct → evaluate → replay`, where the
reconstruction engine is a **pure function** of an `EvidenceBundle`.

## Package layout

```
fie/
  config.py            all tunable constants (signals, bounds, thresholds, model names)
  models.py            pydantic canonical models — the contract every layer shares
  schema.sql           SQLite DDL (Postgres-compatible)
  store.py             normalized store + idempotent upserts + DLQ/checkpoint/drift tables
  simulator/
    scenarios.py       the 8 failure archetypes + their labels (ground truth)
    generate.py        deterministic bundle builder, messy-feed writer, training variants
  ingestion/
    validate.py        raw dict -> canonical model | DLQ reason
    pipeline.py        read -> validate -> dedupe -> DLQ -> drift -> checkpoint; recover_dlq
  reliability.py       data-quality score + the deployment GATE
  agent/
    tools.py           Toolbox: query_telemetry / search_maintenance / find_similar_incidents
    engine.py          RuleBasedEngine (v1.1 buggy, v1.2 fixed) + build_timeline
    features.py        shared feature extractor (train == serve)
    llm.py             LLMEngine base + ClaudeEngine + GrokEngine
    ml_engine.py       MLEngine (trained classifier), same report contract
    reconstruct.py     orchestrator: bundle -> gate -> engine -> RunTrace -> persist
  ml/
    dataset.py         generate a large labeled dataset
    train.py           train + persist the sklearn classifier
  eval/
    golden.py          build/load the labeled golden set
    evaluators.py      correctness / groundedness / timeline / tool-usage / abstention
    harness.py         run an engine over golden set -> EvalReport
  replay/
    replay.py          capture baseline traces; replay a trace vs a new engine
    regression.py      side-by-side diff -> SHIP/HOLD
  web/                 stdlib http.server control-room UI + Jinja templates
  cli.py               all subcommands (demo, ingest, eval, regression, train, serve, ...)
```

## Layer by layer

### 1. Models (`models.py`) — the contract
Everything speaks in these types. The three you must know cold:
- `EvidenceBundle` — the **only** input to an engine (readings + maintenance +
  mes + past_incidents + reliability). Snapshotting it is what makes replay work.
- `IncidentReport` — the output. `cited_ids()` returns every evidence id used;
  grounding = that set ⊆ the bundle's ids.
- `RunTrace` — `inputs` (the bundle) + `tool_calls` + `report`. Persisted to
  `data/runs/RUN-*.json`.

### 2. Store (`store.py`) — idempotency lives here
`upsert_reading` (`store.py`) does `INSERT OR IGNORE` on a primary-key id, then
uses `rowcount` to return `inserted` / `duplicate`, and compares payloads to
detect `conflict`. That one method is the whole dedup story. Also holds the
`dlq`, `checkpoints`, `schema_drift`, and `incidents` tables.

### 3. Simulator (`simulator/`) — ground truth + mess
`scenarios.py` defines 8 archetypes with labels. `generate.py`:
- `build_bundle(sc)` → a clean bundle + labels (used by eval; deterministic).
- `write_raw_feed(...)` → messy JSONL for the store demo (injects dupes,
  out-of-order, impossible values, malformed lines, future timestamps, drift).
- `build_variant(sc, rng, i)` → a jittered instance for the training dataset.

### 4. Ingestion (`ingestion/`) — survive the mess
`pipeline.ingest_file` streams lines, checkpoints by line number, and routes each
record through `validate.py`. Key contract: **a bad record is dead-lettered with
a reason; it never crashes the run** (the validator call is wrapped in
try/except, and identity fields are type-guarded). `recover_dlq` re-drives
dead-lettered rows after applying a field remap — the "fix, then replay the DLQ"
loop.

### 5. Reliability gate (`reliability.py`)
`assess(bundle)` scores telemetry coverage/staleness → `overall`. Below
`config.GATE_MIN_SCORE` it returns `blocked=True` with a reason.

### 6. Reconstruction (`agent/` + `reconstruct.py`)
`reconstruct.reconstruct(bundle, engine)`:
1. `assess` reliability.
2. If blocked → return an `unknown`/`blocked` report (no engine call).
3. Else → `engine.reconstruct(bundle, reliability)` → report + tool calls.
4. Stamp provenance, build a `RunTrace`, persist.

The engines are interchangeable because they all implement
`reconstruct(bundle, reliability) -> (IncidentReport, [ToolCall])`:
- `RuleBasedEngine` (`engine.py`) — deterministic heuristics; `_classify` differs
  by version (the v1.1 bug is here).
- `LLMEngine`/`ClaudeEngine`/`GrokEngine` (`llm.py`) — same summary + strict-JSON
  contract + grounding guard; falls back to rule on any error.
- `MLEngine` (`ml_engine.py`) — a trained classifier predicts the category; the
  timeline/evidence scaffolding is shared via `build_timeline` and the rule
  engine's helpers.

### 7. Evaluation (`eval/`)
`harness.evaluate(engine_name)` runs the engine over `golden.load_golden()` and
scores each case with `evaluators.py`. Exits the CLI non-zero if anything fails
(the CI gate).

### 8. Replay + regression (`replay/`)
`replay.capture_baseline` runs an engine over the golden bundles and saves
traces. `replay.replay_trace(trace, new_engine)` feeds `trace.inputs` to a
candidate. `regression.run_regression(baseline, candidate)` diffs them into
fixed/regressed counts and a SHIP/HOLD verdict.

### 9. UI + CLI (`web/`, `cli.py`)
`web/server.py` renders three read-only views (dashboard, incident, regression)
on the stdlib server. `cli.py` wires every stage to a subcommand; `cmd_demo`
runs the whole loop.

## Where the "impressive" parts actually are
- Idempotent crash recovery → `store.upsert_reading` + `pipeline.ingest_file`.
- Abstention under bad data → `reliability.assess` + the blocked branch in
  `reconstruct.reconstruct`.
- The documented bug + fix → `engine.RuleBasedEngine._classify`.
- Deterministic regression proof → `replay/regression.py`.
- Interchangeable backends with no train/serve skew → `agent/features.py`.
