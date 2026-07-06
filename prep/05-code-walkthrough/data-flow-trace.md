# Data-Flow Trace

Follow a single telemetry reading — and then a single incident — through the
entire system. If you can narrate this from memory, you can whiteboard the whole
project.

## Part A — one telemetry reading, from wire to store

1. **Born (messy).** `simulator/generate.py:write_raw_feed` emits a raw line:
   ```json
   {"kind":"telemetry","machine":"CNC-17","ts":"2026-06-29T08:31:00+00:00",
    "signal":"coolant_flow_lpm","value":18.6,"unit":"metric"}
   ```
   (The same function may also emit a duplicate of it, reorder it, or nearby
   inject an out-of-bounds/malformed/future-timestamp variant.)

2. **Read + checkpoint.** `ingestion/pipeline.py:ingest_file` reads it as line N.
   If N ≤ the stored checkpoint, it's skipped (already committed in a prior run).

3. **Parse.** `json.loads`. If it fails → DLQ reason `malformed_json`, continue.

4. **Validate.** `ingestion/validate.py:validate_telemetry`:
   - unexpected field `unit` → logged as `new_field` drift (tolerated).
   - `machine`/`signal` present & strings? `value` present, numeric, finite?
   - `ts` parses, has a timezone, within the plausible horizon?
   - `signal` known and `value` within `config.SIGNAL_BOUNDS`?
   On any failure → DLQ with a specific reason. On success → a `TelemetryReading`
   with `id = sha1("CNC-17|<ts>|coolant_flow_lpm")[:16]`.

5. **Idempotent upsert.** `store.py:upsert_reading` does `INSERT OR IGNORE`:
   - first time → `inserted`.
   - exact duplicate id → `duplicate` (dedup; no double count).
   - same id, different value → `conflict` → routed to DLQ (first write kept).

6. **Checkpoint advances.** Every `CHECKPOINT_EVERY` lines and at EOF,
   `store.set_checkpoint` records the last committed line. A crash between commit
   and checkpoint is harmless: re-reading those lines just yields `duplicate`.

Net effect: **exactly-once**, even though the feed is at-least-once and corrupt.

## Part B — one incident, from store to SHIP/HOLD

Say we ask about `CNC-17`, window `08:00–08:40`.

1. **Assemble the bundle.** `agent/reconstruct.py:reconstruct_from_store` queries
   readings/maintenance/mes/prior-incidents into an `EvidenceBundle`.

2. **Gate.** `reliability.py:assess(bundle)` computes coverage/staleness. If it's
   below `GATE_MIN_SCORE` (e.g. a telemetry outage), `reconstruct` returns a
   `blocked`/`unknown` report **without running the engine** — it abstains.

3. **Reason.** Otherwise `engine.reconstruct(bundle, reliability)`:
   - `Toolbox.query_telemetry` for each signal → `SignalStats` (baseline, end,
     delta, max_jump) and the evidence ids that best show the signature. These
     calls are *recorded* in `tool_calls`.
   - `_classify` decides the category. With v1.2: coolant dropped **and** temp
     rose → `cooling_degradation`; temp rose with nominal coolant/load →
     `sensor_fault`; etc.
   - `build_timeline`, grounded `supporting_evidence`, `recommended_actions`,
     `missing_evidence`, `similar_incidents`, and a confidence scaled by
     reliability.

4. **Capture.** A `RunTrace` bundles `inputs` (the bundle), `tool_calls`, and the
   `report`, and is written to `data/runs/RUN-*.json`. The incident is saved to
   the store for the UI.

5. **Evaluate.** `eval/harness.py:evaluate` runs the engine over the golden set
   and scores correctness/groundedness/timeline/tool-usage/abstention. On the
   buggy v1.1 engine this **fails** the sensor-fault and overload cases.

6. **Replay + regress.** `replay/regression.py:run_regression("rule-based/1.1.0",
   "rule-based/1.2.0")` captures v1.1 traces, replays their **snapshotted inputs**
   against v1.2, and diffs: `fixed 6, regressed 0 ⇒ SHIP`. Because replay uses the
   snapshot (not the live store), the only variable is the engine — so the diff
   is attributable purely to the change.

## The two sentences that tie it together
- **"Nothing is trusted that shouldn't be."** Bad data dead-letters; low-quality
  windows make the agent abstain.
- **"Nothing ships that I can't prove is safe."** Every run is a replayable
  trace, and a candidate is judged against captured reality before it goes out.
