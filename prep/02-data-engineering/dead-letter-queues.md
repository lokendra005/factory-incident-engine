# Dead-Letter Queues (DLQ)

> **Defend this:** "A record fails validation. Where does it go, and how do you
> get it back?"

---

## What it is

A **dead-letter queue** is a holding area for records that could not be
processed successfully. Instead of two bad options —

- **crash the whole run** on one bad record (brittle: one poison message halts
  ingestion of thousands of good ones), or
- **silently drop** the bad record (invisible data loss: you never know what you
  lost or why) —

you take a third path: **quarantine the record, keep it verbatim, tag it with a
machine-readable reason, and keep going.** The DLQ becomes an auditable inbox of
everything the pipeline couldn't handle. Later, after you fix the upstream issue
or the mapping, you **replay** the DLQ.

The principle this project states up front (`fie/ingestion/__init__.py:5`):

> "nothing silently dropped — every bad record lands in the DLQ with a reason"

That is the whole philosophy in one line: **failure is data, not an exception.**

---

## Why it matters

In a real deployment the source data is guaranteed to contain things your
validator didn't anticipate. If a single malformed line can abort a nightly
ingest, your pipeline is a liability. If bad lines vanish silently, you'll be
debugging "missing data" complaints with no trail. The DLQ solves both: the run
is resilient (one bad record can't stop it) *and* observable (you can count,
inspect, and recover every failure).

For an incident-reconstruction system this is doubly important — the DLQ count
*itself* is a data-quality signal. If 30% of telemetry is dead-lettering, the
reliability gate (`data-quality-and-gating.md`) should refuse to let the agent
draw conclusions.

---

## How THIS project implements it

### The DLQ table

`fie/schema.sql:40-52`:

```sql
CREATE TABLE IF NOT EXISTS dlq (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind TEXT NOT NULL,      -- telemetry | maintenance | mes | unknown
    source_file TEXT DEFAULT '',
    line_no     INTEGER DEFAULT 0,
    raw         TEXT NOT NULL,      -- original payload, verbatim
    reason      TEXT NOT NULL,      -- machine-readable failure reason
    detail      TEXT DEFAULT '',
    ts_ingested TEXT NOT NULL,
    recovered   INTEGER NOT NULL DEFAULT 0
);
```

Three design decisions worth defending:

1. **`raw` is the original payload, verbatim.** We do not store our
   *interpretation* of the bad record — we store exactly what came off the wire.
   That's what makes replay possible: you can re-parse it after fixing the
   mapping (`schema.sql:47`).
2. **`reason` is machine-readable**, `detail` is human-readable. The reason lets
   you aggregate ("how many `out_of_bounds`?"); the detail carries specifics
   (e.g. the exception string).
3. **`source_file` + `line_no`** give you exact provenance — you can point at the
   offending line in the original feed.
4. **`recovered` flag** — a soft-delete/state marker so replayed rows aren't
   re-replayed, and the DLQ retains a full history of what was fixed.

The write path is `store.add_dlq` (`fie/store.py:127-133`).

### Every failure path routes to the DLQ

In `fie/ingestion/pipeline.py`, *every* stage that can reject a record calls
`add_dlq` — nothing is ever dropped:

| Failure | Location | Reason |
|---|---|---|
| Not valid JSON | `pipeline.py:86` | `malformed_json` |
| Valid JSON but not an object | `pipeline.py:92` | `not_an_object` |
| Validator raised an exception | `pipeline.py:106` | `validation_error` |
| Validator returned `model=None` | `pipeline.py:117` | (validator's reason) |
| Same id, different payload | `pipeline.py:143` | `idempotency_conflict` |

### The reasons taxonomy

The `reason` values come from the validators (`fie/ingestion/validate.py`) and
form a deliberate taxonomy. Note the two shapes: **families** (with a `:suffix`)
and **flat reasons**.

| Reason | Meaning | Source |
|---|---|---|
| `malformed_json` | line isn't JSON | `pipeline.py:86` |
| `not_an_object` | JSON but e.g. a list/scalar | `pipeline.py:92` |
| `missing_field:machine` / `:signal` / `:value` / `:kind` / `:event` | required field absent or wrong type | `validate.py:62-67,98-103,121-124` |
| `value_not_numeric` / `value_not_finite` | value isn't a finite number (rejects bool, NaN, inf) | `validate.py:74-77` |
| `out_of_bounds` | value outside physical `SIGNAL_BOUNDS` | `validate.py:82-83` |
| `unknown_signal:<name>` / `unknown_kind:<k>` / `unknown_event:<e>` | value not in the allowed set | `validate.py:79,108-109,130-131` |
| `ts_not_string` / `ts_unparseable` / `ts_naive_no_timezone` / `ts_before_horizon` / `future_timestamp` | timestamp problems | `validate.py:32-44` |
| `schema_missing_value` | the `value`→`reading_c` rename batch | `validate.py:56-58` |
| `validation_error` | validator itself threw | `pipeline.py:106` |
| `idempotency_conflict` | same id, different payload | `pipeline.py:143` |

The stats layer *collapses families to a head* for readable reporting
(`pipeline.py:32-35`):

```python
def _dlq(self, reason: str) -> None:
    # collapse "missing_field:machine" etc. to the family for readable stats
    key = reason.split(":")[0]
    self.dlq[key] = self.dlq.get(key, 0) + 1
```

So the *table* keeps the precise reason (`missing_field:signal`) for debugging,
while the *stats dict* groups by family (`missing_field: 12`) for a human
glance. Precision where you need it, aggregation where you want it.

### DLQ recovery via field remap

This is the payoff and the best story in the project. The simulator injects a
"contiguous bad batch" that renames `value` → `reading_c`
(`generate.py:240-244`) — modeling a real upstream deploy that renamed a field.
Those 12 records dead-letter with reason `schema_missing_value`
(`validate.py:56-58`). Nothing else can save them at ingest time — the value is
under the wrong key.

The fix is `recover_dlq` (`fie/ingestion/pipeline.py:181-216`). It's the "fix,
then replay" loop:

```python
DEFAULT_REMAP = {"reading_c": "value"}   # pipeline.py:178

def recover_dlq(store, remap=None):
    remap = DEFAULT_REMAP if remap is None else remap
    recovered = 0; still_bad = 0
    for row in store.dlq_items(only_unrecovered=True):
        rec = json.loads(row["raw"])            # re-parse the VERBATIM raw
        ...
        for src, dst in remap.items():          # apply the fix
            if src in rec and dst not in rec:
                rec[dst] = rec.pop(src)
        model, reason, _ = validator(rec)        # re-validate
        if model is None:
            still_bad += 1; continue             # un-fixable → stays dead
        store.upsert_reading(model)              # re-drive IDEMPOTENTLY
        store.mark_dlq_recovered(row["id"])
        recovered += 1
    return {"recovered": recovered, "still_dead_lettered": still_bad}
```

Four things make this correct:

1. It re-parses **`row["raw"]`** — the original bytes — which is only possible
   because we stored them verbatim.
2. It applies the **remap** (the "fix"), re-runs the **same validator**, so a
   record now has to pass every other check too (bounds, ts, etc.). A remap can't
   sneak garbage in.
3. It re-drives through the **idempotent upsert**. If the same reading was already
   ingested some other way, this is a `duplicate` no-op — replay is safe. That's
   asserted by `test_dlq_recovery_after_remap` (`test_ingestion.py:41-50`):
   `before < after <= before + res["recovered"]` (count rises, but never by more
   than the number recovered, because some recovered rows dedupe).
4. Rows that *can't* be fixed by the remap stay dead-lettered
   (`still_dead_lettered`) and are never lost — proven by
   `test_recover_dlq_survives_unfixable_garbage` (`test_ingestion.py:68-74`),
   where an un-remappable record (`machine: [1,2]`) survives the recovery pass
   without crashing.

The `recovered=0` filter (`store.dlq_items(only_unrecovered=True)`,
`store.py:135-140`) means replay is itself idempotent: a second `recover_dlq`
won't re-touch already-recovered rows.

---

## Mental model / diagram

```
 INGEST                                    DLQ TABLE (auditable inbox)
 ────────                                  ─────────────────────────────
 line ──► parse ──✗──► add_dlq(raw, "malformed_json")     ┐
        └─► validate ──✗──► add_dlq(raw, "out_of_bounds")  │  raw kept VERBATIM
        └─► upsert ──conflict──► add_dlq(raw,"idempotency…") │  + reason + line_no
                                                            ┘  + recovered=0
                                       │
              (later: upstream fix identified — reading_c was meant to be value)
                                       ▼
 RECOVER   recover_dlq(remap={reading_c: value})
           for each unrecovered row:
              re-parse raw → apply remap → RE-VALIDATE → idempotent upsert
              success → mark recovered=1
              still bad → leave dead-lettered (never lost)
```

Memorize: **"Nothing is dropped. Bad records are quarantined with their raw bytes
and a reason. When the upstream mistake is understood, I remap, re-validate, and
re-drive them idempotently — the fix-then-replay loop."**

---

## Interview questions + strong answers

**Q: Why a DLQ instead of just logging and skipping?**
A: A log line is fire-and-forget — you can't replay a log. The DLQ stores the
*raw payload verbatim* plus a machine-readable reason and exact provenance
(file + line). That makes failures first-class data: I can count them by reason
to spot systemic issues, feed the count into my reliability gate, and — most
importantly — replay them after a fix. Skipping-with-a-log gives you none of
that.

**Q: What guarantees nothing is silently dropped?**
A: Every rejection path in the pipeline calls `add_dlq` — malformed JSON,
non-object, validator exception, validator rejection, and idempotency conflict.
There is no `continue` that discards a record without first writing it to the
DLQ. The `read` counter and the sum of (inserted + duplicate + dlq_total +
conflict) reconcile, so a dropped record would show up as a discrepancy.

**Q: Walk me through DLQ recovery.**
A: The classic case is the `value`→`reading_c` rename. Those records
dead-letter as `schema_missing_value`. Once we know the upstream mistake,
`recover_dlq` applies a `{reading_c: value}` remap, re-parses each stored raw
payload, re-runs the *full* validator (so bounds and timestamp checks still
apply), and re-drives survivors through the idempotent upsert. Fixed rows are
marked `recovered=1`; anything the remap can't fix stays dead-lettered. It's the
"fix upstream, then replay the quarantine" loop.

**Q: What stops recovery from corrupting data or double-inserting?**
A: Two things. Recovered rows go through the same idempotent `INSERT OR IGNORE`,
so re-driving a row that already exists is a `duplicate` no-op — the test asserts
the row count grows by *at most* the number recovered. And the `recovered` flag
plus the `only_unrecovered` filter make the recovery pass itself idempotent, so
running it twice does nothing the second time.

**Q: Why re-run the whole validator during recovery instead of trusting the
remap?**
A: Because a field rename might not be the record's only problem — after
remapping `reading_c` back to `value`, that value still has to be finite, in
bounds, and paired with a valid timestamp and known signal. Re-validating means
recovery can never launder a genuinely bad record into the clean tables just
because one field name was fixed.

**Q: How does the reasons taxonomy help operations?**
A: The precise reason (e.g. `missing_field:signal`) lives in the table for
debugging a specific line; the stats collapse the family (`missing_field`) for a
dashboard-level view (`pipeline.py:32-35`). An operator sees "1% out_of_bounds,
12 schema_missing_value" at a glance, then drills into the exact rows and lines
when needed. It's aggregation for triage, precision for the fix.

**Q: What would you add for production?**
A: A retry/age policy (records that have been dead-lettered N times or M days go
to a "parking lot"), alerting on DLQ rate spikes, a per-reason recovery playbook,
and possibly a separate DLQ per source so one noisy feed can't drown the signal
from another. The schema already has the columns to support all of that.

---

## Resources (real, well-known)

- **Amazon SQS / Amazon EventBridge — "Dead-letter queues"** docs. The canonical
  managed-service framing (redrive policy, max receives, redrive-to-source) that
  `recover_dlq` mirrors.
- **Kafka Connect — "Dead Letter Queue"** (`errors.tolerance`,
  `errors.deadletterqueue.topic.name`). Directly analogous: tolerate errors,
  route bad records to a DLQ topic with headers explaining why.
- **"Enterprise Integration Patterns"** — Hohpe & Woolf. The "Dead Letter
  Channel" and "Invalid Message Channel" patterns are the origin of this design.
- **"Designing Data-Intensive Applications"** — Kleppmann. Fault tolerance and
  poison-message handling in stream processing.
- **Google Cloud Pub/Sub — "Handling message failures"** docs — dead-letter
  topics and replay semantics.
