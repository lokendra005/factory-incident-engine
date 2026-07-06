# Ingestion & ETL

> **Defend this:** "Walk me through how raw plant data becomes something your agent can reason over."

---

## What it is

**ETL / ELT** is the discipline of moving data from messy source systems into a
place where it can be queried and reasoned over, applying structure along the
way.

- **ETL** — *Extract → Transform → Load.* You clean, validate and reshape the
  data **before** it lands in the store. The store only ever holds
  already-canonical rows.
- **ELT** — *Extract → Load → Transform.* You dump raw data into the warehouse
  first (cheap, immutable), then transform it **in place** with SQL/dbt. The
  warehouse holds both raw and derived layers.

**Ingestion** is the "E" and the plumbing around it: reading from the source,
handling failure, and getting bytes into the pipeline reliably.

This project is an **ETL** system with an **ELT-flavored safety net**: records
are transformed (parsed → validated → normalized) before they hit the canonical
tables, but the *raw payload of every rejected record* is preserved verbatim in
a dead-letter queue so it can be re-transformed later. That is the best of both
worlds — clean tables downstream, no lost source data.

---

## Why it matters

For a Forward Deployed Engineer, ingestion is where 80% of the pain of a real
deployment lives. The model/agent is the glamorous part; the reason deployments
fail is that **the customer's data is a lie**: fields get renamed without
warning, timestamps are in three timezones, sensors emit `9999` when they fault,
and half the files are truncated. An FDE who can't defend an ingestion design
can't ship.

The core promise of this layer (`fie/ingestion/__init__.py:1-8`):

> Turns a messy raw feed into trustworthy canonical records. Guarantees:
> * exactly-once *effect* — crash-safe checkpoints + idempotent upserts
> * nothing silently dropped — every bad record lands in the DLQ with a reason
> * schema drift is detected and logged, never crashes the run

Everything downstream (the reliability gate, the reconstruction engine, the
eval harness) is a *pure function of clean canonical models*. That purity is
only possible because ingestion absorbs all the mess first.

---

## ETL vs ELT — how to answer the tradeoff question

| | ETL (this project) | ELT (Snowflake/BigQuery + dbt) |
|---|---|---|
| Transform happens | before load, in Python | after load, in SQL |
| Store holds | only canonical rows | raw + staged + marts |
| Bad data | dead-lettered with reason | lands raw, filtered later |
| Best when | small/edge, strict schema, no warehouse | big data, cheap storage, analyst-driven |
| Cost model | compute up front | storage up front, compute on query |

**Why ETL here:** the engine runs on the edge with **no external services**
(`fie/store.py:1-6` — stdlib `sqlite3` only). There is no elastic warehouse to
"load raw and sort out later," and the downstream consumer is an *agent making a
deployment decision*, so data that reaches the tables must already be
trustworthy. The DLQ is the concession to ELT thinking: we never *destroy* raw
data, we just don't let it into the clean tables until it's fixed.

---

## Batch vs streaming

- **Batch** — process a bounded file/chunk, then stop. Simple, restartable,
  easy to reason about. Latency = batch interval.
- **Streaming** — process an unbounded feed record-by-record as it arrives.
  Low latency, but you inherit hard problems: windowing, out-of-order events,
  backpressure, checkpointing an infinite stream.

This project is **batch over a file that mimics a stream**. `write_raw_feed()`
(`fie/simulator/generate.py:204`) emits JSONL "off the wire," and
`ingest_file()` reads it line by line. But the design is deliberately
**stream-ready**: it processes one record at a time, keeps only `max_ts_seen` in
memory (not the whole file), checkpoints by line, and tolerates out-of-order
arrival. Swap the file handle for a Kafka consumer and the same loop works.

**Interview line:** "It's batch today because the source is a file, but the
processing model is streaming — single-pass, constant memory, checkpointed,
out-of-order-tolerant. Nothing about the core loop assumes the input is
finite."

---

## How THIS project implements it

The whole flow lives in `fie/ingestion/pipeline.py:ingest_file` (lines 59-158).
It is a four-stage pipeline per line, and the stage ordering is the entire
point.

### Stage 0 — Extract (read + resume)

```python
_, start_line = store.get_checkpoint(path.name)   # pipeline.py:67
...
for line_no, line in enumerate(fh, start=1):      # pipeline.py:73
    if line_no <= start_line:                     # skip already-committed lines
        continue
```

The file is opened with `errors="replace"` (`pipeline.py:72`) so even a corrupt
byte can't crash the read. Resume is line-based (see
`checkpoints-and-recovery.md`).

### Stage 1 — Parse

```python
try:
    rec = json.loads(raw)                          # pipeline.py:84
except (json.JSONDecodeError, ValueError):
    store.add_dlq(kind_hint, raw, "malformed_json", ...)
    ...
if not isinstance(rec, dict):
    store.add_dlq(kind_hint, raw, "not_an_object", ...)
```

A line that isn't valid JSON, or is valid JSON but not an object (e.g. a bare
`[1,2]` or `null`), is dead-lettered — **not** raised.

### Stage 2 — Route + Validate

```python
kind = rec.get("kind", kind_hint)                  # pipeline.py:101
validator = VALIDATORS.get(kind, VALIDATORS[kind_hint])
try:
    model, reason, drift = validator(rec)          # pipeline.py:104
except Exception as exc:                            # defensive by design
    store.add_dlq(kind_hint, raw, "validation_error", detail=str(exc)[:200], ...)
```

`VALIDATORS` (`fie/ingestion/validate.py:139-143`) is a dispatch table:
`telemetry`, `maintenance`, `mes`. Each validator returns
`(model | None, reason, drift)`. Crucially, the `except Exception` means *even a
bug in a validator* dead-letters one line instead of aborting a multi-thousand-
line run. See `schema-drift-and-validation.md` for why this defensiveness is
load-bearing.

### Stage 3 — Normalize (inside the validator)

Validation and normalization are fused. `validate_telemetry`
(`validate.py:49-89`) doesn't just check the record — it **produces the
canonical model**, minting the deterministic id and normalizing the timestamp:

```python
return dt.isoformat(), ""                           # validate.py:46 (normalized UTC iso)
...
reading = TelemetryReading(
    id=_rid(machine, ts, signal), machine=machine, ts=ts,
    signal=signal, value=float(value),              # validate.py:85-88
)
```

The canonical models (`fie/models.py:25-57`) are strict Pydantic types. Once a
record is a `TelemetryReading`, nothing downstream ever sees the raw mess again
(`models.py:1-11`).

### Stage 4 — Load (idempotent upsert)

```python
status = store.upsert_reading(model)               # pipeline.py:131
if status == "inserted":  stats.inserted += 1
elif status == "duplicate": stats.duplicate += 1
elif status == "conflict":  ...                      # dead-lettered too
```

The upsert returns `inserted | duplicate | conflict`. See
`idempotency-and-exactly-once.md` — this is how re-processing is safe.

### Canonical models — the contract

The transform target is defined once, in `fie/models.py`:

- `TelemetryReading` (id, machine, ts, signal, value, source)
- `MaintenanceRecord` (id, machine, ts, kind, component, note, closed, ...)
- `MesEvent` (id, machine, ts, event, detail, code, source)

These roll up into an `EvidenceBundle` (`models.py:74-83`) which is the *pure
input* to the whole reasoning layer. **The canonical model is the boundary
between "data engineering" and "everything else."**

### Config as single source of truth

The simulator and the validator agree on what "impossible" means because both
read `config.SIGNAL_BOUNDS` (`fie/config.py:27-34`). The generator uses it to
know when it's injecting a bad value; the validator uses it to reject one
(`validate.py:79-83`). "If a sensor bound changes, it changes in exactly one
place" (`config.py:5-6`). This is a real data-engineering best practice:
**contract-driven validation, not hard-coded magic numbers.**

---

## Mental model / diagram

```
 raw JSONL "off the wire"          fie/ingestion/pipeline.py::ingest_file
 (telemetry.jsonl)                 ────────────────────────────────────
        │
        ▼
   ┌──────────┐   bad bytes / not-JSON     ┌───────────────┐
   │  PARSE   │ ─────────────────────────► │      DLQ      │
   └────┬─────┘   reason=malformed_json    │ (raw + reason │
        │                                  │  preserved)   │
        ▼                                  └───────▲───────┘
   ┌──────────┐   invalid / bad type              │
   │ VALIDATE │ ─────────────────────────────────┘
   │+NORMALIZE│   reason=out_of_bounds, ...   (drift logged separately)
   └────┬─────┘
        │  canonical model (TelemetryReading/…)
        ▼
   ┌──────────┐   INSERT OR IGNORE
   │  UPSERT  │ ──► inserted | duplicate | conflict(→DLQ)
   └────┬─────┘
        │  checkpoint every N lines
        ▼
   canonical tables  ──►  EvidenceBundle  ──►  reliability gate ──► agent
   (clean, typed)         (pure input)
```

The mental model to memorize: **"Extract, then a funnel of three sieves —
parse, validate/normalize, load — where anything caught by a sieve falls into
the DLQ with a labeled reason, and nothing is ever dropped on the floor."**

---

## Interview questions + strong answers

**Q: Is this ETL or ELT, and why?**
A: ETL — transform-before-load — because the store is an embedded SQLite with no
elastic warehouse to "load raw and clean later," and the downstream consumer is
an agent making a ship/no-ship call, so tables must be trustworthy on read. But
I keep an ELT-style safety valve: the DLQ preserves the raw payload of every
rejected record, so I never destroy source data — I just gate it out of the
clean tables until it's fixed and replayed.

**Q: Why fuse validation and normalization instead of separate steps?**
A: Because the act of proving a record is valid is the same as producing its
canonical form. `validate_telemetry` checks bounds/types *and* mints the
deterministic id and UTC-normalized timestamp in one pass
(`validate.py:85-88`). Separating them would mean two traversals and a window
where a "validated" record still isn't canonical. Fusing them makes "valid" and
"canonical" the same state.

**Q: What happens to the order of the four stages if you reorder them?**
A: It breaks. You can't validate before you parse (you don't have a dict yet),
and you can't upsert before you validate (you'd mint an id from garbage or crash
model construction). The ordering — parse, then validate/normalize, then upsert
— is a strict dependency chain, and each stage has its own DLQ exit so a failure
at any stage is contained to one line.

**Q: How would you make this streaming?**
A: It essentially already is. The loop is single-pass, constant-memory (only
`max_ts_seen` and a line counter are retained), checkpointed, and
out-of-order-tolerant. I'd replace the file iterator with a Kafka/Kinesis
consumer, move the checkpoint from `(source_file, line_no)` to
`(topic, partition, offset)`, and commit the offset only after the DB commit —
same exactly-once-effect story I already have.

**Q: Where's the schema contract, and who owns it?**
A: `fie/config.py` and `fie/models.py`. `SIGNAL_BOUNDS` and `NOMINAL` are the
physical contract shared by simulator and validator; the Pydantic models are the
structural contract. Contract lives in one place so producer and consumer can't
silently disagree — which is the single most common cause of production data
incidents.

**Q: What's the blast radius of one malformed record?**
A: One line. Every stage is wrapped so a failure dead-letters the single
offending record and the run continues. That's proven by
`test_bad_typed_record_dead_letters_not_crashes` — a record with `machine: 999`
and a nested-object `component` lands in the DLQ while the good records around it
still insert.

---

## Resources (real, well-known)

- **"Designing Data-Intensive Applications"** — Martin Kleppmann (O'Reilly).
  Chapters 10-11 (Batch & Stream processing) are the canonical reference for
  everything on this page.
- **"The Data Warehouse Toolkit"** — Kimball & Ross. The ETL vs ELT and
  dimensional-modeling bible.
- **dbt documentation** (getdbt.com) — the reference implementation of modern
  ELT / transform-in-warehouse.
- **Apache Kafka documentation** — "Delivery Semantics" and consumer offset
  management; directly analogous to this project's checkpoint model.
- **"Fundamentals of Data Engineering"** — Reis & Housley (O'Reilly). Strong
  modern overview of the ingestion → transform → serve lifecycle.
- **Pydantic docs** (docs.pydantic.dev) — the validation/normalization boundary
  this project uses for its canonical models.
