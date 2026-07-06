# Checkpoints & Crash Recovery

> **Defend this:** "Your ingest job is halfway through a 10,000-line file and the
> box gets OOM-killed. What happens when it restarts?"

---

## What it is

A **checkpoint** is durable bookmark that records how far a job has successfully
progressed, so that after a crash the job can **resume** from that point instead
of starting over (wasteful) or skipping ahead (data loss).

**Crash recovery** is the property that a job can be killed at *any* instant and,
on restart, converge to the same final state as if it had never crashed. The two
failure modes it must avoid:

- **Reprocessing / double-counting** — re-doing work already done, corrupting
  results.
- **Data loss / gaps** — skipping work that wasn't actually finished.

The key insight this project embodies: **a checkpoint on its own is not enough.**
A checkpoint can only be advanced *atomically* with the work it protects, which
is hard across two systems (a file and a DB). This project sidesteps the hard
atomicity problem by making the work **idempotent** — so it doesn't matter if the
checkpoint is slightly behind the data; re-doing the overlap is a no-op. This is
the same equation as `idempotency-and-exactly-once.md`, viewed from the recovery
angle.

---

## Why it matters

Long-running ingestion *will* be interrupted — deploys, OOM kills, spot-instance
reclaims, network blips, `Ctrl-C`. A pipeline without checkpoints either:

1. restarts from zero (unacceptable for large feeds — hours of rework), or
2. tracks progress but *incorrectly*, producing duplicates or gaps that
   silently poison every downstream analysis.

For an FDE deploying at a customer site, "what happens when it crashes" is one of
the first questions a serious ops team will ask. The answer here is
demonstrable, not hand-waved — there's a test that literally simulates a crash.

---

## How THIS project implements it

### The checkpoint store

Checkpoints live in their own table (`fie/schema.sql:54-61`):

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
    source_file TEXT PRIMARY KEY,
    byte_offset INTEGER NOT NULL DEFAULT 0,
    line_no     INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
```

One row per source file. `line_no` is the last **committed** line;
`byte_offset` is advisory. The comment (`schema.sql:54-55`) states the goal:
"Enables crash-safe resume with no reprocessing and no data loss."

Read/write helpers in `fie/store.py`:

- `get_checkpoint` (`store.py:152-157`) — returns `(byte_offset, line_no)`, or
  `(0, 0)` if the file was never seen.
- `set_checkpoint` (`store.py:159-168`) — an idempotent upsert on `source_file`
  via `ON CONFLICT(source_file) DO UPDATE`, and it commits immediately so the
  bookmark is durable.

### Resume on startup

`ingest_file` reads the checkpoint before the loop and skips already-committed
lines (`fie/ingestion/pipeline.py:65-74`):

```python
start_line = 0
if resume:
    _, start_line = store.get_checkpoint(path.name)
    stats.resumed_from_line = start_line
...
for line_no, line in enumerate(fh, start=1):
    if line_no <= start_line:      # already committed in a prior run
        continue
```

### Advance the checkpoint — *after* committing the data

The single most important ordering in the whole file
(`pipeline.py:148-157`):

```python
committed = line_no
if committed % config.CHECKPOINT_EVERY == 0:
    store.conn.commit()                       # 1) durably commit the rows
    store.set_checkpoint(path.name, 0, committed)  # 2) then bookmark them
...
store.conn.commit()
if committed:
    store.set_checkpoint(path.name, path.stat().st_size, committed)
```

`CHECKPOINT_EVERY` is 500 lines (`fie/config.py:59`). The config comment makes
the design explicit (`config.py:57-59`):

> "Crash safety does not depend on this value because idempotency makes
> reprocessing harmless."

That sentence is the entire thesis. **The checkpoint frequency is a
performance knob, not a correctness knob.** Checkpoint every line and you do more
writes but less rework after a crash; checkpoint every 10,000 lines and you do
fewer writes but re-read more after a crash — but the *final state is identical
either way*, because the re-read lines all dedupe.

### Why line-based, not byte-offset

The `byte_offset` column exists but is deliberately not used for resume
mid-stream. The comment explains (`pipeline.py:151-153`):

> "resume is line-based; byte offset is advisory (0 mid-stream to avoid
> fh.tell(), which Python disables during line iteration)."

Python raises `OSError` if you call `fh.tell()` while iterating a text file line
by line, so a byte-precise checkpoint would fight the language. Line numbers are
robust, human-readable, and — because JSONL is one record per line — map cleanly
onto "records processed." The final checkpoint does record the true byte size
(`path.stat().st_size`) as a completeness marker.

### Why crash recovery is *safe*, not just *possible*

Checkpoint + idempotency interlock. Consider the crash window between "commit
rows" and "set checkpoint":

- Rows for lines 1001-1500 are committed to `telemetry`.
- Crash **before** `set_checkpoint` advances to 1500 (it still says 1000).
- Restart: resume from line 1001, re-process 1001-1500.
- Every one of those upserts hits `INSERT OR IGNORE`, finds the id already
  present, and returns `duplicate` — **zero new rows, zero corruption.**

So the "cost" of a crash is re-reading at most `CHECKPOINT_EVERY` lines, all of
which are harmless duplicates. There is **no window** in which a crash causes
either loss or double-count. (If the ordering were reversed — checkpoint first,
data second — a crash *would* skip the uncommitted rows. The order is the
correctness guarantee.)

### The crash test (proof, not promise)

`tests/test_ingestion.py:29-38`:

```python
def test_crash_midway_no_double_count(store, raw_dir):
    d, _ = raw_dir
    ingest_all(store, raw_dir=d)
    final = store.counts()["telemetry"]
    # simulate a crash: rewind the checkpoint and re-drive
    store.set_checkpoint("telemetry.jsonl", 0, 1500)
    out = ingest_file(store, d / "telemetry.jsonl")
    assert out.inserted == 0            # all re-seen rows are duplicates
    assert out.duplicate > 0
    assert store.counts()["telemetry"] == final
```

This is the money shot for the interview. It **manually rewinds the checkpoint**
to line 1500 (simulating a crash that lost checkpoint progress but kept committed
data) and re-drives the file. The assertions prove the theory: `inserted == 0`
(nothing new), `duplicate > 0` (the rewound region was re-seen), and the total
row count is *unchanged* from the clean run. Crash recovery is not asserted by
comment — it's a passing test.

Its sibling `test_resume_is_idempotent` (`test_ingestion.py:19-27`) proves the
happy path: re-running a fully-ingested feed reads **zero** lines
(`out["telemetry.jsonl"]["read"] == 0`) because the checkpoint already points at
the end. And `test_checkpoint_roundtrip` (`tests/test_store.py:15-18`) proves the
store layer persists and returns the bookmark correctly.

---

## Mental model / diagram

```
 FILE (telemetry.jsonl)                CHECKPOINTS table
 line 1     ✓ committed                source_file  line_no
 ...                                   telemetry    1000
 line 1000  ✓ committed  ◄── bookmark
 line 1001  ✓ committed  ┐
 ...                     │ committed to DB, but crash hit
 line 1500  ✓ committed  ┘ BEFORE checkpoint advanced to 1500
 line 1501  ✗ not reached          ← CRASH

 RESTART:
   get_checkpoint() → 1000
   skip lines ≤ 1000
   re-process 1001..1500  ── every upsert → "duplicate" → NO-OP
   process 1501..EOF      ── new rows → "inserted"
   final state == as if no crash ✓

 ORDERING RULE (the correctness guarantee):
   commit(rows)  THEN  set_checkpoint(line)
   crash in the gap ⇒ safe re-read (idempotent)
   reverse the order ⇒ data loss
```

Memorize: **"Commit the data, then move the bookmark. Anything re-read in the gap
is an idempotent duplicate, so the checkpoint frequency is a performance knob,
never a correctness knob."**

---

## Interview questions + strong answers

**Q: Walk me through a crash mid-file.**
A: On restart, `ingest_file` reads the checkpoint (`get_checkpoint`), which is the
last *committed* line, and skips everything up to and including it. Because I
commit DB rows *before* advancing the checkpoint, a crash in that gap means I
re-read some already-committed lines — but each one hits `INSERT OR IGNORE`,
finds its deterministic id already present, and returns `duplicate`. So the
re-read is a harmless no-op and the final row count is identical to a clean run.
`test_crash_midway_no_double_count` proves exactly this.

**Q: Why not checkpoint every single line for maximum safety?**
A: Because safety doesn't depend on frequency — idempotency already guarantees
correctness regardless of where the checkpoint sits. Frequency only trades write
overhead against post-crash rework. `CHECKPOINT_EVERY = 500` is a throughput
choice; I could set it to 1 or 10,000 and get the same final state, just
different performance. The config comment says this explicitly.

**Q: Why line numbers instead of byte offsets?**
A: Two reasons. First, JSONL is one record per line, so a line number *is* a
record count — clean semantics. Second, Python disables `fh.tell()` during
line-by-line iteration (it raises `OSError`), so a byte-precise mid-stream
checkpoint would require abandoning the natural iterator. Line-based resume is
robust and readable; I still stamp the true byte size at completion as a
"fully consumed" marker.

**Q: What if the checkpoint advanced but the data commit was lost?**
A: That can't happen in my ordering — I commit rows first, checkpoint second, and
`set_checkpoint` itself commits. The only reorder that would cause loss is
checkpoint-before-data, which I specifically avoid. If I *did* have that bug,
lines between the stale data and the advanced checkpoint would be skipped
forever — silent data loss, the worst kind. Ordering is the guardrail.

**Q: How does this generalize to a streaming source like Kafka?**
A: Directly. The checkpoint becomes the consumer offset per (topic, partition).
I'd commit the offset only *after* the DB write commits, so the same "data
first, bookmark second" ordering holds. Kafka redelivers uncommitted offsets on
rebalance — which is at-least-once — and my idempotent upsert absorbs the
redeliveries. Same equation, different bookmark.

**Q: What's the recovery cost — how much work is redone?**
A: Bounded by `CHECKPOINT_EVERY` — at most ~500 lines re-read, all of which
short-circuit as duplicates. It's O(checkpoint interval), not O(file size). That
bound is what makes "just re-run it" a legitimate recovery strategy here.

---

## Resources (real, well-known)

- **"Designing Data-Intensive Applications"** — Kleppmann. Chapter 8
  ("The Trouble with Distributed Systems") and Chapter 11 on checkpointing and
  fault tolerance in stream processors.
- **Apache Flink documentation — "Checkpointing" & "State Backends"** — the
  reference architecture for checkpoint-based exactly-once recovery; Flink's
  barrier/snapshot model is the production version of this idea.
- **Kafka documentation — "Consumer offset management"** — the offset-as-
  checkpoint pattern this project's line_no mirrors.
- **"Streaming Systems"** — Akidau, Chernyak & Lax (O'Reilly). Deep on
  watermarks, checkpoints, and exactly-once in unbounded data.
- **SQLite docs — WAL mode & atomic commit** — why the `conn.commit()` ordering
  gives durable, crash-consistent checkpoints (see `databases-sqlite-postgres.md`).
