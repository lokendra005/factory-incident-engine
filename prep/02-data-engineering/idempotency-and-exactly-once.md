# Idempotency & Exactly-Once

> **Defend this:** "You claim exactly-once. Prove it — networks lose acks and
> processes crash mid-write."

---

## What it is

**Idempotency**: an operation you can apply many times and get the same result
as applying it once. `x = 5` is idempotent; `x += 1` is not. An idempotent
*write* is one where re-delivering the same record leaves the store unchanged.

**Delivery semantics** — the three guarantees a message system can offer:

- **At-most-once** — deliver, don't retry. Fast, but you lose data on failure.
- **At-least-once** — retry until acked. No data loss, but **duplicates** when an
  ack is lost and the sender retries a message the receiver already processed.
- **Exactly-once** — every record takes effect exactly one time. The holy grail,
  and *almost always a lie* at the transport level.

The critical distinction every senior engineer must draw:

> **True exactly-once *delivery* is effectively impossible** across an unreliable
> network (you can't atomically "process AND ack"). What you actually build is
> **at-least-once delivery + idempotent processing = exactly-once *effect*.**

The message may arrive twice; the *outcome* happens once. This project is
explicit about that in `fie/ingestion/pipeline.py:6` — "Net effect:
exactly-once" — and in `fie/ingestion/__init__.py:5` — "exactly-once *effect*".
The word *effect* is doing enormous work and you should say it out loud in the
interview.

---

## Why it matters

Retries are not optional in a real system — networks drop acks, consumers
rebalance, processes get OOM-killed mid-batch. So duplicates are not an edge
case, they are the **normal steady state** of any at-least-once pipeline. If your
writes aren't idempotent, retries corrupt your data: double-counted telemetry,
inflated metrics, an agent reasoning over a machine that "vibrated twice."

The simulator injects duplicates *on purpose* (`generate.py:247-249`, ~2% exact
re-emits) precisely so the ingestion layer has to prove it dedupes them.

---

## How THIS project implements it

Three pieces combine: a **deterministic id** (the idempotency key), an
**INSERT OR IGNORE + rowcount** write, and **conflict detection** for the case
where the same key shows up with a *different* payload.

### 1. The deterministic idempotency key

The id is a content hash of the record's *natural key*, minted during validation
(`fie/ingestion/validate.py:28-29`):

```python
def _rid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
```

For telemetry the natural key is `(machine, ts, signal)`
(`validate.py:87`):

```python
id=_rid(machine, ts, signal)
```

Maintenance keys on `(machine, ts, component, kind)` (`validate.py:111`); MES on
`(machine, ts, event, code)` (`validate.py:133`).

**Why this is the whole trick:** the id is a *pure function of the record's
identity*. The same physical event, replayed through validation any number of
times, produces the **same 16-hex id every time**. The simulator uses the
identical `_rid` (`generate.py:35-36`), so the id is stable from generation
through ingestion. No coordination, no central sequence, no dedup cache — the
key *is* the dedup.

This is the "natural key / business key" pattern. It is superior to a random
UUID-per-message because two independent emissions of the same real-world event
collapse to one row automatically.

### 2. INSERT OR IGNORE + rowcount = branch-free dedup

`store.upsert_reading` (`fie/store.py:75-89`):

```python
cur.execute(
    "INSERT OR IGNORE INTO telemetry (id, machine, ts, signal, value, source) "
    "VALUES (?,?,?,?,?,?)",
    (r.id, r.machine, r.ts, r.signal, r.value, r.source),
)
if cur.rowcount == 1:
    return "inserted"
existing = self.conn.execute(
    "SELECT value, ts FROM telemetry WHERE id=?", (r.id,)
).fetchone()
if existing and (abs(existing["value"] - r.value) > 1e-9 or existing["ts"] != r.ts):
    return "conflict"
return "duplicate"
```

The idempotency is enforced by the schema: `id TEXT PRIMARY KEY`
(`fie/schema.sql:6`, and comment at `schema.sql:3` — "Idempotency is enforced by
PRIMARY KEY on the deterministic event id"). `INSERT OR IGNORE` is SQLite's
"insert if the primary key is free, otherwise do nothing." Then `cur.rowcount`
tells us which happened:

- `rowcount == 1` → the row was new → **`inserted`**.
- `rowcount == 0` → the id already existed → it's a re-delivery. Now we
  distinguish:
  - payload matches → **`duplicate`** (harmless, safe to ignore).
  - payload differs → **`conflict`**.

The database's uniqueness constraint does the dedup atomically; we never
read-then-write (which would race). This is the correct, race-free way to
implement upsert-if-absent.

### 3. Conflict detection — dedup's serious sibling

A `duplicate` is boring (same id, same data — expected under at-least-once). A
`conflict` is *interesting*: the same natural key arrived with **different
values**. That means either a real data-quality problem upstream or a hash
collision. The policy is **keep-first, surface-loud** (`store.py:7-11`):

> "duplicate" means the exact same id was already stored (safe to re-deliver).
> "conflict" means the same id arrived with a *different* payload — we keep the
> first write and surface the conflict, because silently overwriting would
> corrupt history.

And the pipeline dead-letters conflicts so a human can look
(`pipeline.py:141-146`):

```python
elif status == "conflict":
    stats.conflict += 1
    store.add_dlq(kind_hint, raw, "idempotency_conflict",
                  detail="same id, different payload; kept first", ...)
```

This is a subtle, mature choice. Naive upserts do `INSERT ... ON CONFLICT DO
UPDATE` (last-write-wins), which **silently destroys history**. This project
refuses to overwrite and instead raises the anomaly to the DLQ. In an
incident-reconstruction system, "the coolant reading for 14:03 changed after the
fact" is exactly the kind of thing you must never hide.

The contract is nailed down by `test_idempotency_contract`
(`tests/test_store.py:8-12`):

```python
assert store.upsert_reading(_r("a", 55.0)) == "inserted"
assert store.upsert_reading(_r("a", 55.0)) == "duplicate"   # exact re-delivery
assert store.upsert_reading(_r("a", 99.0)) == "conflict"    # same id, new value
assert store.counts()["telemetry"] == 1                     # first write kept
```

### 4. Checkpoint + idempotent replay = exactly-once effect

Idempotency alone gives you *safe retries*. Combined with a **checkpoint**, it
gives you *exactly-once effect across a crash*. The pipeline commits DB rows and
then advances the checkpoint (`pipeline.py:148-157`):

```python
committed = line_no
if committed % config.CHECKPOINT_EVERY == 0:
    store.conn.commit()
    store.set_checkpoint(path.name, 0, committed)
...
store.conn.commit()
if committed:
    store.set_checkpoint(path.name, path.stat().st_size, committed)
```

The ordering matters: **commit the data, then advance the checkpoint.** If we
crash *between* those two, the next run re-reads some already-committed lines —
but every re-read row hits `INSERT OR IGNORE` and returns `duplicate`, changing
nothing. So the window of "reprocessing" is harmless *by construction*. If we
crashed the other way (checkpoint first, data second) we'd lose rows. This is the
classic **at-least-once + idempotent = exactly-once effect** equation, and the
checkpoint is what makes the "at-least-once" bounded instead of "re-read the
whole file." See `checkpoints-and-recovery.md` for the crash test.

---

## Mental model / diagram

```
 record ──► validate ──► _rid(machine|ts|signal) = deterministic id
                                     │  (same event → same id, always)
                                     ▼
                        INSERT OR IGNORE INTO telemetry (id PRIMARY KEY, ...)
                                     │
                     ┌───────────────┴───────────────┐
              rowcount == 1                     rowcount == 0
                  │                                   │
              "inserted"                    SELECT existing payload
                                                      │
                                        ┌─────────────┴─────────────┐
                                  payload same                payload differs
                                      │                             │
                                  "duplicate"                  "conflict"
                                  (no-op, safe)            (keep first + DLQ)


 CRASH SAFETY:   commit rows ─► advance checkpoint
                 crash in the gap ⇒ re-read some lines ⇒ all "duplicate" ⇒ no harm
                 ────────────────────────────────────────────────────────────
                 at-least-once delivery  +  idempotent write  =  exactly-once EFFECT
```

Memorize the equation: **at-least-once + idempotent write = exactly-once
effect.** Every design choice on this page serves that equation.

---

## Interview questions + strong answers

**Q: You say "exactly-once." I don't believe you.**
A: You're right to push — exactly-once *delivery* is essentially impossible over
an unreliable channel, because you can't atomically process a message and ack
it. What I guarantee is exactly-once *effect*: delivery is at-least-once, so a
record may be processed more than once, but the write is idempotent — keyed on a
deterministic content hash and applied via `INSERT OR IGNORE` — so a re-processed
record is a no-op. The outcome happens once even though the message might arrive
twice.

**Q: Why a deterministic hash id instead of a UUID per message?**
A: A random UUID makes every emission unique, which defeats dedup — two copies of
the same real event would get two rows. My id is `sha1(machine|ts|signal)`, a
pure function of the event's natural key, so the same physical event always
collapses to one id no matter how many times it's emitted or replayed. The dedup
is free and needs no state.

**Q: Why not `ON CONFLICT DO UPDATE` (last-write-wins)?**
A: Because last-write-wins silently overwrites history. In an incident
reconstruction system, a reading changing after the fact is a red flag, not
something to bury. So I keep the first write and route the mismatch to the DLQ as
an `idempotency_conflict`. Duplicates are silent (expected); conflicts are loud
(anomalous). That's the distinction at `store.py:82-89`.

**Q: What's the difference between `duplicate` and `conflict` in your code?**
A: Both mean the id already existed (`rowcount == 0`). `duplicate` = the stored
payload matches the incoming one, so it's a benign re-delivery. `conflict` = same
id, *different* value or ts, which means either upstream corruption or a hash
collision — I keep the original and dead-letter the newcomer. The float compare
uses an epsilon (`abs(...) > 1e-9`) so floating-point noise isn't mistaken for a
real conflict.

**Q: Where could the exactly-once-effect guarantee break?**
A: Two places. (1) If I advanced the checkpoint *before* committing the rows, a
crash would skip uncommitted lines — data loss. I commit rows first, checkpoint
second, so the crash window only causes harmless re-reads. (2) If the id weren't
truly deterministic — say it included wall-clock time or a random salt — replays
wouldn't dedupe. Both are avoided by design.

**Q: Is SHA-1 a problem here?**
A: Not for this use. SHA-1 is broken for *adversarial collision resistance*
(you can't trust it against an attacker forging collisions), but here it's a
non-adversarial content fingerprint over structured plant data, truncated to
16 hex chars for compactness. Accidental collisions are astronomically unlikely
at this scale, and if one did occur, my conflict-detection path catches it as an
`idempotency_conflict` rather than silently merging — so even a collision fails
safe.

**Q: How would this map to Kafka?**
A: Same equation. Kafka gives at-least-once by default (consumer commits offsets
after processing). I'd keep the deterministic-id idempotent upsert exactly as-is,
and move the checkpoint from `(source_file, line_no)` to the consumer offset,
committing the offset only after the DB commit. Kafka's own "exactly-once" is
transactions across its brokers; for a heterogeneous sink like my DB,
idempotent-write-plus-offset-commit is the robust pattern.

---

## Resources (real, well-known)

- **"Designing Data-Intensive Applications"** — Kleppmann. Chapter 11 ("Stream
  Processing"), the "Exactly-once execution" and "Idempotence" sections, is the
  definitive treatment of everything on this page.
- **Kafka documentation — "Message Delivery Semantics"** and **"Exactly-Once
  Semantics"** (KIP-98). The canonical industry framing of at-most/at-least/
  exactly-once.
- **Nathan Marz, "How to beat the CAP theorem"** and the Lambda Architecture
  writing — early influential argument for idempotent, recomputable pipelines.
- **Google Cloud Dataflow / Apache Beam docs** — "Exactly-once processing" —
  explains the effect-vs-delivery distinction clearly in a production system.
- **SQLite docs — "INSERT" (ON CONFLICT clause)** — the exact semantics of
  `INSERT OR IGNORE` and `changes()`/rowcount used here.
