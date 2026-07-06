# Databases: SQLite here, Postgres-ready

> **Defend this:** "You built a production-shaped incident engine on SQLite?
> Really?"
> **Answer:** "Yes — deliberately. And swapping to Postgres is a connection-
> layer change, not a rewrite."

---

## What it is

A **relational database** stores data in typed tables with constraints
(primary keys, foreign keys, uniqueness) and lets you query it with SQL,
providing **ACID transactions** (Atomicity, Consistency, Isolation, Durability).

- **SQLite** — an *embedded* database. No server process; the whole DB is a
  single file, accessed in-process via a library. Zero-ops, zero-dependency.
- **PostgreSQL** — a *client/server* database. A separate process that many
  clients connect to over the network; supports high concurrency, replication,
  rich types, and heavy analytical workloads.

The design stance of this project: **write portable, Postgres-shaped SQL, run it
on SQLite for the demo, keep the swap-to-Postgres path a one-file change.**

---

## Why it matters

An FDE constantly makes the "how heavy a datastore does this actually need"
call. Reaching for Postgres/Kafka/Snowflake on day one of a proof-of-concept is a
classic over-engineering mistake — it adds ops burden, deployment friction, and
"can't run it on my laptop" pain, all before you've proven the idea. But building
something so SQLite-specific that productionizing means a rewrite is the opposite
mistake. The mature move is **start embedded, stay portable** — and being able to
articulate exactly *when* and *how* you'd graduate to Postgres.

---

## How THIS project implements it

### Why SQLite was the right call here

`fie/store.py:1-6`:

> "Kept deliberately dependency-free (stdlib `sqlite3`) so the whole engine runs
> with no external services. The DDL in schema.sql is Postgres-compatible; only
> the connection layer here is SQLite-specific."

Concrete payoffs for *this* system:

1. **Zero external services** — the demo, the eval harness, and CI run with
   nothing to install and no network. This matters because the whole engine is
   designed to degrade to a deterministic offline path (`config.py:70-76`: LLM
   backends fall back to rule-based so "the demo/eval/CI never require a network
   or a key"). A server DB would break that self-contained promise.
2. **`sqlite3` is in the Python stdlib** — no dependency at all for the store.
3. **The workload fits** — single-writer batch ingestion of a small fleet
   (`config.MACHINES` is 4 machines, `config.py:22`). SQLite comfortably handles
   this; it's fast for local, mostly-single-writer workloads.
4. **A file is trivially snapshot-able** — copy `plant.db` and you have the whole
   state, which suits a reproducible demo.

This is right-sizing, and you should defend it as a *choice*, not a limitation.

### Postgres-compatible DDL

The schema (`fie/schema.sql`) is written to port cleanly. The header comment
(`schema.sql:1-3`):

> "Portable SQL (SQLite default; the DDL is intentionally close to Postgres so
> the store can be swapped). Idempotency is enforced by PRIMARY KEY on the
> deterministic event id."

Portability choices in the DDL:

- `TEXT`, `INTEGER`, `REAL` — types that exist in both. Timestamps stored as
  ISO-8601 `TEXT` (`schema.sql:8`), which sorts lexicographically *and*
  chronologically because of the fixed ISO format — portable and index-friendly.
- `PRIMARY KEY`, `NOT NULL`, `DEFAULT`, `CREATE INDEX IF NOT EXISTS` — standard
  across both engines.
- `INTEGER PRIMARY KEY AUTOINCREMENT` on the DLQ (`schema.sql:43`) is the one
  SQLite-ism; the Postgres equivalent is `BIGSERIAL`/`GENERATED AS IDENTITY`.
  That's a mechanical substitution, called out below.

### Indexes — designed for the actual query patterns

The store's read paths are all "one machine, a time window, sometimes a signal"
(`store.py:194-227`). The indexes match exactly (`schema.sql:13-14`):

```sql
CREATE INDEX ix_tel_machine_ts     ON telemetry (machine, ts);
CREATE INDEX ix_tel_machine_sig_ts ON telemetry (machine, signal, ts);
```

- `query_readings(machine, start, end)` → served by `(machine, ts)`
  (`store.py:194-203`).
- `query_readings(machine, start, end, signal)` → served by the composite
  `(machine, signal, ts)` (`store.py:198-201`).
- `signal_coverage` groups by signal within a machine → the composite covers it
  (`store.py:229-237`).

These are **composite indexes ordered by selectivity/access pattern** (equality
column `machine` first, then range column `ts`) — textbook index design, and it
transfers unchanged to Postgres. The incidents table has `ix_inc_asset (asset,
window_start)` (`schema.sql:85`) for the `prior_incidents` similarity lookup
(`store.py:263-278`).

### WAL — Write-Ahead Logging

`store.py:47`:

```python
self.conn.execute("PRAGMA journal_mode=WAL;")
```

**What WAL is:** instead of writing changes to a rollback journal and modifying
the main DB file in place, SQLite appends changes to a separate write-ahead log
and later checkpoints them into the main file. Two benefits that matter here:

1. **Readers don't block writers and writers don't block readers.** In the
   default rollback-journal mode, a write locks the whole DB. With WAL, a reader
   sees a consistent snapshot while a write is in progress — important because the
   store is opened with `check_same_thread=False` (`store.py:44`) and a UI can
   read while ingestion writes.
2. **Better crash durability and throughput** for the commit-heavy ingestion loop
   (a commit every `CHECKPOINT_EVERY` lines).

WAL is conceptually the *same idea* as Postgres's own WAL (Postgres is
WAL-based by default and builds replication on top of it), so the mental model
transfers directly. `foreign_keys=ON` (`store.py:48`) is also set because SQLite
disables FK enforcement by default — a portability gotcha worth knowing.

### Transactions

The store uses explicit commits and a transaction context manager
(`store.py:64-72`):

```python
@contextmanager
def tx(self):
    cur = self.conn.cursor()
    try:
        yield cur
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
```

Transactions are what make the checkpoint story correct: a batch of upserts is
committed atomically, *then* the checkpoint advances (`pipeline.py:149-157`) — so
a crash can never leave rows half-committed relative to the bookmark
(see `checkpoints-and-recovery.md`). The `INSERT OR IGNORE` upsert
(`store.py:75-89`) relies on the PRIMARY KEY constraint being enforced
transactionally, which is ACID behavior identical in both engines. The float
epsilon in conflict detection (`abs(...) > 1e-9`, `store.py:87`) guards against
`REAL`/`double precision` representation differences — portable defensive coding.

### The one connection-layer seam

Everything SQLite-specific is in `Store.__init__` (`store.py:39-50`): the
`sqlite3.connect`, the `Row` factory, and the PRAGMAs. The DDL is loaded from
`schema.sql` as text (`store.py:31`). To swap to Postgres you'd:

1. Replace the `sqlite3.connect(...)` block with `psycopg`/SQLAlchemy engine
   creation (WAL/`foreign_keys` PRAGMAs drop — Postgres does both natively).
2. Change `AUTOINCREMENT` → `GENERATED ... AS IDENTITY`, and `INSERT OR IGNORE`
   → `INSERT ... ON CONFLICT (id) DO NOTHING` (Postgres's spelling of the same
   idempotent insert — and `cur.rowcount` semantics carry over).
3. Swap `?` placeholders for `%s`.

The *queries* (`store.py:187-278`), the *idempotency logic*, the *schema shape*,
and the *indexes* are unchanged. That's the payoff of writing portable SQL from
day one.

---

## When/how to swap to Postgres

Reach for Postgres when the workload outgrows the embedded assumptions:

| Trigger | Why SQLite strains | Postgres answer |
|---|---|---|
| **Concurrent writers** (many ingesters) | SQLite serializes writes (one writer at a time) | MVCC, row-level locking |
| **Network/multi-host access** | it's a local file, not a server | client/server over TCP |
| **Large data / heavy analytics** | single-file, limited parallelism | partitioning, parallel query, big shared buffers |
| **Replication / HA / failover** | none built in | streaming replication, standbys |
| **Rich types** (JSONB, arrays, `timestamptz`, GIN indexes) | limited type system | native, indexable |
| **Fine-grained access control** | file permissions only | roles, row-level security |

For this project specifically: the trigger would be *multiple concurrent
ingestion workers* or a *multi-tenant* deployment. Until then, SQLite is the
correct, lower-friction choice — and because the seam is one file, you graduate
without a rewrite.

---

## Mental model / diagram

```
  APPLICATION (pipeline, reliability, agent, queries)
        │  speaks: portable SQL + idempotent upsert contract
        ▼
  ┌────────────────────────────────────────────────┐
  │  Store (fie/store.py)  ── the ONE seam ──        │
  │  __init__: connect + WAL + FK PRAGMAs  ← SQLite-specific
  │  queries / upserts / checkpoints       ← portable
  └────────────────────────────────────────────────┘
        │                                   swap seam →
        ▼                                              ▼
   SQLite (today)                              Postgres (graduation)
   • one file: plant.db                        • client/server
   • embedded, zero-ops                        • concurrent writers, MVCC
   • WAL: readers ∥ writer                     • WAL + replication native
   • INSERT OR IGNORE                          • INSERT … ON CONFLICT DO NOTHING
   • AUTOINCREMENT                             • GENERATED AS IDENTITY
        └──────── same DDL shape, same indexes, same idempotency ────────┘
```

The one-liner: **"SQLite because the workload is single-writer batch on a small
fleet and I value zero-ops, offline, reproducible runs. Portable DDL and a
one-file connection seam mean Postgres is a swap, not a rewrite — I graduate when
I need concurrent writers, HA, or heavy analytics."**

---

## Interview questions + strong answers

**Q: Why SQLite for something that's supposed to look production-grade?**
A: Right-sizing. The workload is single-writer batch ingestion over four
machines, and a core goal is that the demo, eval, and CI run offline with no
services — SQLite (stdlib, one file) delivers that with zero ops. I didn't paint
myself into a corner: the DDL is deliberately Postgres-compatible and all the
SQLite specifics are in one `__init__`, so productionizing is a connection-layer
swap, not a rewrite. Choosing the lighter tool *and* keeping the exit ramp is the
senior move.

**Q: What exactly changes when you move to Postgres?**
A: Three mechanical things: the connect block (drop the WAL/foreign-key PRAGMAs —
Postgres does both natively), `AUTOINCREMENT` → `GENERATED AS IDENTITY`, and
`INSERT OR IGNORE` → `INSERT ... ON CONFLICT (id) DO NOTHING`, plus `?` → `%s`
placeholders. The schema shape, the indexes, the idempotency contract, and every
query stay the same. That's the dividend of writing portable SQL up front.

**Q: What is WAL and why did you enable it?**
A: Write-Ahead Logging — changes are appended to a separate log and checkpointed
into the main file later, instead of mutating it in place. It lets readers see a
consistent snapshot without blocking the writer, which matters because I open the
connection with `check_same_thread=False` so a UI can read while ingestion
writes. It also improves durability and throughput for my commit-heavy loop. It's
the same concept Postgres uses natively, so the model transfers.

**Q: Explain your index choices.**
A: Every read is "one machine, a time range, optionally one signal," so I built
composite indexes `(machine, ts)` and `(machine, signal, ts)` — equality column
first, range column last, which is the optimal order for these predicates. The
first serves whole-window queries, the second serves signal-filtered queries and
the per-signal coverage rollup. These indexes are engine-agnostic and move to
Postgres unchanged.

**Q: How do transactions relate to your correctness guarantees?**
A: The checkpoint story depends on them. I commit a batch of idempotent upserts
atomically, then advance the checkpoint. Because the commit is ACID, a crash can
never leave rows partially applied relative to the bookmark — worst case I re-read
a committed batch and every row dedupes. The idempotent `INSERT OR IGNORE` itself
relies on the PRIMARY KEY constraint being enforced transactionally, which is
identical in SQLite and Postgres.

**Q: What's a portability gotcha you handled?**
A: A couple. SQLite disables foreign-key enforcement by default, so I set
`PRAGMA foreign_keys=ON` explicitly. And storing timestamps as ISO-8601 TEXT
gives me lexical == chronological ordering that's index-friendly and portable,
rather than relying on a SQLite-specific date type. In Postgres I'd likely
upgrade those to `timestamptz`, which the query layer wouldn't notice.

**Q: When would SQLite actually fall over here?**
A: The moment I need concurrent writers — multiple ingestion workers or a
multi-tenant setup — because SQLite serializes writes to one at a time. Also if I
needed network access from multiple hosts, replication/HA, or heavy analytical
queries. None of those are true for the current single-writer, small-fleet,
offline-demo scope, so SQLite is correct *now*, and the seam makes the upgrade
cheap *when* those triggers hit.

---

## Resources (real, well-known)

- **SQLite documentation** — especially **"Appropriate Uses For SQLite"**,
  **"Write-Ahead Logging"**, and **"When to use SQLite"**. Directly justifies the
  right-sizing argument.
- **PostgreSQL documentation** — **"Reliability and the Write-Ahead Log"** and
  **"INSERT ... ON CONFLICT"**; the Postgres side of every swap on this page.
- **"Designing Data-Intensive Applications"** — Kleppmann. Chapters 3 (storage
  engines, B-trees, LSM) and 7 (transactions, isolation levels).
- **"Use The Index, Luke!"** (use-the-index-luke.com) — Markus Winand's practical
  guide to composite indexes and access-pattern-driven index design.
- **"Database Internals"** — Alex Petrov (O'Reilly). Deep on storage engines,
  WAL, and B-trees for defending the mechanics.
- **SQLite "Full-Featured SQL" & datatype docs** — the type-affinity model, which
  explains why the portable `TEXT/INTEGER/REAL` DDL behaves as it does.
