-- Canonical, normalized plant store. Portable SQL (SQLite default; the DDL is
-- intentionally close to Postgres so the store can be swapped). Idempotency is
-- enforced by PRIMARY KEY on the deterministic event id.

CREATE TABLE IF NOT EXISTS telemetry (
    id      TEXT PRIMARY KEY,
    machine TEXT NOT NULL,
    ts      TEXT NOT NULL,          -- ISO-8601 UTC
    signal  TEXT NOT NULL,
    value   REAL NOT NULL,
    source  TEXT NOT NULL DEFAULT 'telemetry'
);
CREATE INDEX IF NOT EXISTS ix_tel_machine_ts ON telemetry (machine, ts);
CREATE INDEX IF NOT EXISTS ix_tel_machine_sig_ts ON telemetry (machine, signal, ts);

CREATE TABLE IF NOT EXISTS maintenance (
    id         TEXT PRIMARY KEY,
    machine    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    component  TEXT NOT NULL,
    note       TEXT DEFAULT '',
    closed     INTEGER NOT NULL DEFAULT 1,
    technician TEXT DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'maintenance'
);
CREATE INDEX IF NOT EXISTS ix_maint_machine_ts ON maintenance (machine, ts);

CREATE TABLE IF NOT EXISTS mes (
    id      TEXT PRIMARY KEY,
    machine TEXT NOT NULL,
    ts      TEXT NOT NULL,
    event   TEXT NOT NULL,
    detail  TEXT DEFAULT '',
    code    TEXT DEFAULT '',
    source  TEXT NOT NULL DEFAULT 'mes'
);
CREATE INDEX IF NOT EXISTS ix_mes_machine_ts ON mes (machine, ts);

-- Dead-letter queue: records that failed validation. Kept with the raw
-- payload and a reason so they can be inspected and REPLAYED after a fix.
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

-- Ingestion checkpoints: (source_file) -> byte offset + line already committed.
-- Enables crash-safe resume with no reprocessing and no data loss.
CREATE TABLE IF NOT EXISTS checkpoints (
    source_file TEXT PRIMARY KEY,
    byte_offset INTEGER NOT NULL DEFAULT 0,
    line_no     INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);

-- Schema-drift log: fields we did not expect, or expected fields gone missing.
CREATE TABLE IF NOT EXISTS schema_drift (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind TEXT NOT NULL,
    field       TEXT NOT NULL,
    kind        TEXT NOT NULL,      -- new_field | missing_field | type_change
    detail      TEXT DEFAULT '',
    ts          TEXT NOT NULL
);

-- Persisted incident reports (the agent's output), indexed for similarity.
CREATE TABLE IF NOT EXISTS incidents (
    incident_id         TEXT PRIMARY KEY,
    asset               TEXT NOT NULL,
    window_start        TEXT NOT NULL,
    window_end          TEXT NOT NULL,
    root_cause_category TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 0,
    summary             TEXT DEFAULT '',
    report_json         TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inc_asset ON incidents (asset, window_start);
