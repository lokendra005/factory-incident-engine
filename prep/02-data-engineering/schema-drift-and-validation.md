# Schema Drift & Validation

> **Defend this:** "The customer renames a field in prod on a Friday afternoon.
> What does your pipeline do?"

---

## What it is

**Schema** is the agreed shape of your data — field names, types, allowed values.
**Schema drift** (a.k.a. schema evolution) is the reality that this shape
*changes over time* without asking your permission: someone adds a field, renames
one, changes a type, or stops sending one. In a real deployment, drift is not an
"if," it's a "when."

**Validation** is the gate that decides, per record, whether the data matches the
contract well enough to become a canonical model. The central design question is
what to do when it doesn't, and drift forces a nuanced answer:

- **Tolerate additive change** — a *new*, unexpected field should not break
  ingestion (Postel's Law: "be liberal in what you accept").
- **Reject subtractive/type change safely** — a *missing required* field or a
  *wrong type* should be rejected **per-record into the DLQ, not crash the run**.

The distinction — **tolerate new, reject missing/bad, and never let either abort
the batch** — is the entire subject of this file.

---

## Why it matters

Drift is the number-one silent killer of data pipelines. Two failure modes an
FDE must design against:

1. **Brittle validation** — a strict schema check that throws on the first
   unexpected field halts a 5,000-line ingest because line 3,001 gained a
   harmless `unit` field. Now the whole night's data is missing.
2. **Blind validation** — accepting anything, so a renamed or dropped field
   silently produces nulls/garbage that poison downstream analysis, and nobody
   notices until a model gives a nonsense answer.

The mature answer is *observable tolerance*: accept what you safely can, reject
what you can't, **log the drift either way**, and keep running. This project does
exactly that.

---

## How THIS project implements it

### Validation happens *before* model construction — the HARD lesson

The single most important architectural choice: **validators check the raw dict
and reject bad data cleanly, so that a strict Pydantic model is only ever
constructed from data known to be well-typed.** A model-construction failure on a
weird field must **dead-letter one record, not crash the run.**

Look at `validate_maintenance` (`fie/ingestion/validate.py:92-115`). The comment
states the lesson (`validate.py:96-97`):

```python
# identity fields must be strings; wrong types are data corruption, not a
# model bug -> reject cleanly rather than letting model construction raise.
if not isinstance(machine, str) or not machine:
    return None, "missing_field:machine", []
...
if not isinstance(component, str) or not component:
    return None, "missing_field:component", []
```

If instead we passed the raw dict straight into `MaintenanceRecord(**rec)`, a
record with `machine: 999` and `component: {"nested": "obj"}` would raise a
Pydantic `ValidationError` *during construction*. The type checks up front turn
"a crash" into "a clean `None` return with a reason" — which becomes a single
DLQ entry.

### Defense in depth — the pipeline's try/except backstop

Even with pre-checks, the pipeline wraps the validator call in a catch-all
(`fie/ingestion/pipeline.py:103-111`):

```python
try:
    model, reason, drift = validator(rec)
except Exception as exc:  # noqa: BLE001 - defensive by design
    store.add_dlq(kind_hint, raw, "validation_error",
                  detail=str(exc)[:200], source_file=path.name, line_no=line_no)
    stats._dlq("validation_error")
    committed = line_no
    continue
```

The comment (`pipeline.py:98-100`) is the thesis of this whole file:

> "The validator must never crash the run — any unexpected error (e.g. a
> model-construction failure on a weirdly typed field) dead-letters the single
> offending line and moves on."

So there are **two layers**: the validator's own type guards (the primary
defense, giving *specific* reasons), and the pipeline's `except Exception`
backstop (the safety net, giving `validation_error` for anything unforeseen). A
bad record can fail at most one line — never the batch.

This is proven by `test_bad_typed_record_dead_letters_not_crashes`
(`tests/test_ingestion.py:53-65`): a file of `[good, bad, good]` where the bad
record has `machine: 999` and `component: {"nested": "obj"}`. The assertions:
`st.inserted == 1` (the two good records share one id), `st.dlq_total == 1` (the
bad one is dead-lettered), and the run **does not raise**.

### Drift detection — tolerate new, reject missing

`validate_telemetry` (`fie/ingestion/validate.py:49-89`) shows both drift
directions explicitly.

**Tolerate a new field** (`validate.py:52-55`):

```python
_TEL_CORE = {"machine", "ts", "signal", "value"}
_TEL_KNOWN = _TEL_CORE | {"kind", "source", "unit"}
...
for k in rec:
    if k not in _TEL_KNOWN:
        drift.append((k, "new_field", f"unexpected field '{k}'"))
```

An unexpected field is **recorded as `new_field` drift and otherwise ignored** —
the record still validates and becomes a canonical model. The simulator injects
this: from ~60% of the stream onward it adds a `unit` field
(`generate.py:236-239`). Note `unit` is in `_TEL_KNOWN`, so it doesn't even count
as drift — it's an *anticipated* additive field. A truly unknown field (say
`foo`) would be logged as drift but still tolerated. This is Postel's Law made
concrete.

**Reject a missing required field, with a *specific* drift reason**
(`validate.py:56-58`):

```python
if "value" not in rec and "reading_c" in rec:
    drift.append(("value", "missing_field", "value renamed to 'reading_c'"))
    return None, "schema_missing_value", drift
```

This is the modeled "someone renamed `value` to `reading_c`" batch
(`generate.py:240-244`). The validator doesn't just reject it — it *diagnoses*
it: it logs `missing_field` drift with the human hint "value renamed to
'reading_c'" **and** returns the reason `schema_missing_value`, so the DLQ row
tells a future operator exactly what happened. That diagnosis is what makes the
DLQ remap-recovery possible (`dead-letter-queues.md`).

### The drift log

Drift observations are returned as a `(field, kind, detail)` list and persisted
by the pipeline (`pipeline.py:112-114`):

```python
for fld, dkind, detail in drift:
    store.record_drift(kind_hint, fld, dkind, detail)
    stats.drift += 1
```

`store.record_drift` (`fie/store.py:171-182`) **de-dupes**: it only logs a given
`(source_kind, field, kind)` combination once. Without that, the `unit` field on
thousands of records would write thousands of identical drift rows. The schema
(`schema.sql:63-71`) defines the three drift kinds: `new_field | missing_field |
type_change`. `test_full_ingest_survives_mess` asserts `store.drift_items()` is
non-empty after a full run (`test_ingestion.py:16`).

### The full validation gauntlet (telemetry)

For the record, a telemetry record must survive *all* of these to become a model
(`validate.py:49-89`), each with its own reason:

1. drift scan (tolerate new fields)
2. rename detection (`schema_missing_value`)
3. `machine`, `signal` present and string (`missing_field:*`)
4. `value` present (`missing_field:value`)
5. timestamp: string, parseable, tz-aware, within horizon
   (`ts_not_string` / `ts_unparseable` / `ts_naive_no_timezone` /
   `ts_before_horizon` / `future_timestamp`, `validate.py:32-46`)
6. value is a finite number, not a bool (`value_not_numeric` /
   `value_not_finite`, `validate.py:74-77`)
7. signal is known (`unknown_signal:*`)
8. value within physical bounds (`out_of_bounds`)

Only then is the `TelemetryReading` constructed (`validate.py:85-88`). Note the
subtle `isinstance(value, bool)` check — in Python `True` is an `int`, so without
that guard a boolean would sneak past the numeric check. That's the kind of
detail that shows real validation care.

### Timestamps: reject, don't assume

A naive (no-timezone) timestamp is **rejected**, not silently assumed to be UTC
(`validate.py:39-40`, reason `ts_naive_no_timezone`). Asserted by
`test_validator_rejects_bad_records` (`test_ingestion.py:84-86`). Silently
assuming UTC is a classic bug that corrupts time-series data across DST/timezone
boundaries — the project refuses to guess.

---

## Mental model / diagram

```
 raw dict
    │
    ▼  drift scan
 unknown field?  ──yes──► log drift(new_field)  ──► KEEP GOING (tolerate)
    │no
    ▼
 value missing but reading_c present?
    │yes ─► log drift(missing_field, "renamed") ─► DLQ reason=schema_missing_value
    │no
    ▼  TYPE + PRESENCE GUARDS  (before any model construction)
 machine/signal/value ok?  ──no──► return (None, "missing_field:*")  ──► DLQ
 ts tz-aware & in horizon?  ──no──► return (None, "future_timestamp"…) ─► DLQ
 value finite number?       ──no──► return (None, "value_not_finite")  ─► DLQ
 in bounds & known signal?  ──no──► return (None, "out_of_bounds")     ─► DLQ
    │ all pass
    ▼
 TelemetryReading(...)   ← model constructed ONLY from proven-clean data
    │
    ▼  (any unforeseen exception anywhere above)
 pipeline try/except  ──► DLQ reason=validation_error   ← never crashes the run
```

The one-liner: **"Validate the raw dict first, construct the strict model only
from proven-clean data, tolerate additive drift with a log, reject subtractive/
type drift into the DLQ with a specific reason — and wrap it all so no single
record can ever abort the batch."**

---

## Interview questions + strong answers

**Q: A new field appears mid-stream. What happens?**
A: It's tolerated. The telemetry validator scans for fields outside the known set
and records a `new_field` drift observation, but the record still validates and
becomes a canonical model — additive changes shouldn't break ingestion (Postel's
Law). The drift log is de-duped so I get one row per new field, not one per
record. If the new field were in my anticipated set (like `unit`), it wouldn't
even register as drift.

**Q: A required field disappears — say it got renamed. What happens?**
A: The record is rejected into the DLQ, but *diagnostically*. For the modeled
`value`→`reading_c` rename, the validator detects that `value` is absent while
`reading_c` is present, logs a `missing_field` drift with the hint "value renamed
to reading_c," and returns reason `schema_missing_value`. That diagnosis is what
lets `recover_dlq` later remap and replay those rows. So drift here isn't just
survived — it's *explained*.

**Q: Why validate the raw dict instead of just constructing the Pydantic model
and catching the error?**
A: Because a raw `ValidationError` gives a generic, noisy failure, and relying on
construction-time exceptions means the *model* is your validator — which couples
your data-quality logic to your type definitions. I check presence and types up
front so I can return a *specific* machine-readable reason
(`missing_field:component` vs a stack trace), and the strict model is only ever
built from data I've already proven clean. The pipeline still has an
`except Exception` backstop for anything I didn't foresee — defense in depth.

**Q: The HARD lesson — what breaks if you skip the type guards?**
A: A record like `{machine: 999, component: {nested: obj}}` would raise inside
`MaintenanceRecord(**rec)`. If that propagated, one corrupt line kills the whole
run and every good record after it is lost. The guards turn that into a clean
`None`-return that dead-letters one line.
`test_bad_typed_record_dead_letters_not_crashes` proves the good records around
it still insert.

**Q: Why reject a naive timestamp instead of assuming UTC?**
A: Because assuming UTC is a guess, and a wrong guess silently shifts a
time-series by hours — catastrophic for a system that reconstructs incidents by
correlating events in time. If the timezone is missing, the data contract was
violated, so I dead-letter it with `ts_naive_no_timezone` and make the problem
visible rather than baking in a silent error.

**Q: How do you tell drift from corruption?**
A: Drift is a *structural* change to the schema (a field added, renamed,
retyped) — I log it and, if additive, tolerate it. Corruption is a *value*
problem within a known schema (out of bounds, NaN, unparseable ts) — I reject it.
Both go observable: drift to the `schema_drift` table, corruption to the DLQ. The
rename case is interesting because it's structural drift that *manifests* as a
missing required field, so it gets both a drift log and a DLQ entry.

**Q: How would you evolve the schema deliberately?**
A: Add the new field to `_TEL_KNOWN` (so it stops registering as drift), extend
the canonical Pydantic model with a defaulted optional field so old records still
validate, and — for a rename — add the mapping to the recovery remap. The
config-driven contract means the change is localized. For a breaking type change
I'd version the source_kind and route to a new validator.

---

## Resources (real, well-known)

- **Postel's Law (the Robustness Principle)** — "be conservative in what you
  send, liberal in what you accept." The philosophical basis for
  tolerate-new-field.
- **Confluent Schema Registry docs — "Schema Evolution and Compatibility"**
  (BACKWARD / FORWARD / FULL compatibility). The industry-standard framework for
  exactly the tolerate-vs-reject decisions on this page.
- **Pydantic docs** (docs.pydantic.dev) — validation, strict types, and why
  construction-time validation is powerful but must be guarded.
- **"Designing Data-Intensive Applications"** — Kleppmann, Chapter 4 ("Encoding
  and Evolution") — the definitive treatment of schema evolution and
  backward/forward compatibility.
- **Great Expectations** (greatexpectations.io) — a production data-validation
  framework; useful vocabulary (expectations, data docs) for talking about
  validation as a first-class artifact.
- **Apache Avro / Protobuf schema evolution rules** — concrete examples of
  additive-safe vs breaking changes.
