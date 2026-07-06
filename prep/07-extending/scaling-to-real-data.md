# Scaling to Real Plant Data

How this prototype becomes a real deployment. Useful for the "where would you
take this next?" question.

## 1. Replace the simulator with real connectors
The simulator emits raw JSONL; real plants emit through protocols. Swap
`simulator/write_raw_feed` for connectors that produce the same raw records:
- **OPC-UA** (most modern PLCs/SCADA) — subscribe to tags, map to
  `{machine, ts, signal, value}`.
- **MQTT / Sparkplug B** — common for IIoT telemetry.
- **Modbus/TCP** — older equipment.
- **Historian exports** (OSIsoft PI / AVEVA, Ignition) — batch CSV/API.
- **MES/ERP** (SAP, etc.) — for maintenance and MES events.

The ingestion layer downstream **doesn't change** — it already assumes messy,
duplicated, out-of-order, schema-drifting input. That's the whole point.

## 2. Swap SQLite → Postgres (or a time-series DB)
- `schema.sql` is already Postgres-compatible. Change the connection in
  `store.py` (`sqlite3` → `psycopg`), parameter style `?` → `%s`.
- For high-rate telemetry, consider **TimescaleDB** (Postgres extension) or a
  historian; keep maintenance/mes/incidents in relational tables.

## 3. Make ingestion continuous
- Today: batch over files with line checkpoints.
- Real: consume from **Kafka/Kinesis/Pub-Sub**. The idempotency key + DLQ +
  checkpoint concepts port directly (offset = checkpoint; consumer group handles
  resume). Exactly-once *effect* still comes from idempotent upserts.

## 4. Real labels for eval + training
- The golden set is currently generated. Replace it with **real, human-labeled
  incidents** (post-mortems, work orders with confirmed root causes). This is the
  highest-leverage data you can collect — it's how you *trust* any engine.
- Feed the same labels into `ml/` to train on reality instead of simulation.

## 5. Reliability gate with real SLAs
- `reliability.assess` currently scores coverage/staleness from the bundle.
  Extend with real signals: sensor health, calibration status, network gaps,
  known-bad tags. Tie `GATE_MIN_SCORE` to a real operational policy.

## 6. Observability + human-in-the-loop
- Run traces already capture inputs/tools/output. Ship them to a real store and
  add: latency, token cost (LLM path), and **human feedback** ("was this root
  cause correct?"). That feedback closes the loop — it becomes new golden labels.
- Add an approval step for high-impact recommended actions (the "escalation
  engine" idea) before anything is actioned automatically.

## 7. Deployment
- Containerize (Dockerfile exists), run behind a real WSGI/ASGI server instead of
  the stdlib one, put the store on managed Postgres, schedule ingestion.
- Keep the CI eval gate — it's your regression safety net when models/prompts
  change.

## The through-line
Every "prototype" choice here (SQLite, file feed, synthetic labels, stdlib UI)
was picked so the *architecture* is real while the *dependencies* stay zero. Each
can be swapped independently without touching the layers above it — which is
exactly what makes it a credible starting point rather than a throwaway demo.
