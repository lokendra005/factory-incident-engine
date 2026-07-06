# Data model

All canonical models live in `fie/models.py`. Raw feed records are untyped
dicts; once a record passes validation it becomes one of these and nothing
downstream ever sees the mess again.

## Canonical records

| Model | Identity (idempotency key) | Notes |
|---|---|---|
| `TelemetryReading` | `sha1(machine\|ts\|signal)` | one row per signal per sample |
| `MaintenanceRecord` | `sha1(machine\|ts\|component\|kind)` | inspection/repair/replace/lube/calibration |
| `MesEvent` | `sha1(machine\|ts\|event\|code)` | startup/shutdown/config_change/error_code/… |

The id is a pure function of content, so the same record delivered twice
collapses to one row (dedup) and a record with the same id but a different
payload is flagged as a **conflict** rather than silently overwritten.

## EvidenceBundle — the unit of reasoning

```
EvidenceBundle
├─ asset, window_start, window_end
├─ readings[]        TelemetryReading   (in window)
├─ maintenance[]     MaintenanceRecord  (lookback history)
├─ mes[]             MesEvent           (in window)
├─ past_incidents[]  PriorIncident      (for find_similar_incidents)
└─ reliability{}     per-source scores
```

This is the **only** input to the engine. Snapshotting it into the run trace is
what makes replay deterministic.

## IncidentReport — the output

```
IncidentReport
├─ root_cause, root_cause_category, confidence
├─ timeline[]            TimelineEntry (ts, severity, evidence_ids)
├─ supporting_evidence[] Evidence      (id, kind, summary)   ← grounding
├─ missing_evidence[]    what would raise confidence
├─ recommended_actions[]
├─ similar_incidents[]
├─ data_reliability, blocked, blocked_reason   ← gate provenance
└─ engine, agent_version, prompt_version, generated_at
```

`cited_ids()` returns every evidence id referenced anywhere in the report. The
groundedness evaluator checks that this set is a subset of the ids actually
present in the bundle — i.e. the agent cannot invent evidence.

## RunTrace — the replay record

`RunTrace` bundles `inputs` (the `EvidenceBundle`), the ordered `tool_calls`, and
the `report`, plus engine/prompt versions. Stored as JSON under `data/runs/`.

## Store schema

`fie/schema.sql` defines `telemetry`, `maintenance`, `mes`, plus the operational
tables `dlq`, `checkpoints`, `schema_drift`, and `incidents`. DDL is written to
be Postgres-compatible; only the connection layer in `store.py` is SQLite.
