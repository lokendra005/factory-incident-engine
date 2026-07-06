# Architecture

```
                          ┌─────────────────────────────────────────┐
  simulator/  ──raw JSONL─▶│  ingestion/                              │
  (messy feed)             │  parse → validate → drift → dedupe → DLQ │
                           └───────────────┬──────────────────────────┘
                                           │ canonical records
                                           ▼
                                   store.py  (SQLite, idempotent)
                                           │
                     EvidenceBundle (window snapshot) ──────────┐
                                           │                     │
                                           ▼                     │
                              reliability.py  (GATE)             │
                                           │ blocked? ──yes──▶ "unknown / blocked" report
                                           │ no                  │
                                           ▼                     │
                           agent/  engine.reconstruct(bundle)    │
                        (query_telemetry / search_maintenance /  │
                         find_similar_incidents)                 │
                                           │                     │
                                 IncidentReport + ToolCalls      │
                                           ▼                     │
                                RunTrace  (inputs snapshot) ◀────┘
                                     │            │
                          eval/ ─────┘            └───── replay/  (feed inputs to a new engine)
                     (score vs golden)                   → regression report → SHIP / HOLD
```

## Key idea: the engine is pure

`engine.reconstruct(bundle) -> (IncidentReport, [ToolCall])` reads **only** the
`EvidenceBundle` handed to it. It never touches the store or the clock. Two
consequences:

1. **Evaluation is reproducible** — the same bundle always yields the same
   verdict.
2. **Replay is deterministic** — the trace snapshots the bundle, so a candidate
   engine can be run against the exact inputs the baseline saw. Any output
   difference is caused by the engine change and nothing else.

## Data flow, concretely

- The **simulator** emits raw, id-less JSONL exactly as a real feed would, then
  corrupts it on purpose (see [failure-model](failure-model.md)).
- **Ingestion** assigns a *deterministic* canonical id (`sha1(machine|ts|signal)`
  for telemetry). Idempotency and dedup fall out of that id being a primary key.
- The **store** is plain SQLite with Postgres-compatible DDL. Swapping to
  Postgres is a connection change, not a rewrite.
- **Reconstruction** builds a bundle for an `(asset, window)`, asks the
  reliability gate whether the data is trustworthy, and either returns a blocked
  report or runs the engine.
- Every run is written to `data/runs/RUN-*.json` — inspectable and git-friendly.

## Why stdlib for the web layer

The engine's only hard dependencies are `pydantic` and `jinja2`. The UI runs on
`http.server`, so `pip install -r requirements.txt && fie serve` works with
nothing else to provision. Choosing "runs in one command, no services" over a
heavier framework is deliberate — it is the property that makes the project
trivially runnable by a reviewer.
