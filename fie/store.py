"""SQLite-backed normalized store.

Kept deliberately dependency-free (stdlib ``sqlite3``) so the whole engine runs
with no external services. The DDL in schema.sql is Postgres-compatible; only
the connection layer here is SQLite-specific.

Idempotency contract: ``upsert_*`` returns one of "inserted" | "duplicate" |
"conflict". "duplicate" means the exact same id was already stored (safe to
re-deliver). "conflict" means the same id arrived with a *different* payload —
we keep the first write and surface the conflict, because silently overwriting
would corrupt history.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from . import config
from .models import (
    IncidentReport,
    MaintenanceRecord,
    MesEvent,
    PriorIncident,
    TelemetryReading,
)

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: Optional[Path] = None):
        config.ensure_dirs()
        self.db_path = Path(db_path or config.DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            self.db_path, check_same_thread=False, timeout=30.0
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def reset(self) -> None:
        """Drop all data (used by `fie simulate --reset` and tests)."""
        cur = self.conn.cursor()
        for tbl in ("telemetry", "maintenance", "mes", "dlq", "checkpoints",
                    "schema_drift", "incidents"):
            cur.execute(f"DELETE FROM {tbl}")
        self.conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Cursor]:
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- canonical upserts (idempotent) ------------------------------------
    def upsert_reading(self, r: TelemetryReading) -> str:
        cur = self.conn.cursor()
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

    def upsert_maintenance(self, m: MaintenanceRecord) -> str:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO maintenance "
            "(id, machine, ts, kind, component, note, closed, technician, source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (m.id, m.machine, m.ts, m.kind, m.component, m.note,
             int(m.closed), m.technician, m.source),
        )
        if cur.rowcount == 1:
            return "inserted"
        row = self.conn.execute(
            "SELECT note, closed, technician FROM maintenance WHERE id=?", (m.id,)
        ).fetchone()
        if row and (row["note"] != m.note or bool(row["closed"]) != m.closed
                    or row["technician"] != m.technician):
            return "conflict"
        return "duplicate"

    def upsert_mes(self, e: MesEvent) -> str:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO mes (id, machine, ts, event, detail, code, source) "
            "VALUES (?,?,?,?,?,?,?)",
            (e.id, e.machine, e.ts, e.event, e.detail, e.code, e.source),
        )
        if cur.rowcount == 1:
            return "inserted"
        row = self.conn.execute(
            "SELECT detail, code FROM mes WHERE id=?", (e.id,)
        ).fetchone()
        if row and (row["detail"] != e.detail or row["code"] != e.code):
            return "conflict"
        return "duplicate"

    # -- dead-letter queue -------------------------------------------------
    def add_dlq(self, source_kind: str, raw: str, reason: str, detail: str = "",
                source_file: str = "", line_no: int = 0) -> None:
        self.conn.execute(
            "INSERT INTO dlq (source_kind, source_file, line_no, raw, reason, detail, ts_ingested) "
            "VALUES (?,?,?,?,?,?,?)",
            (source_kind, source_file, line_no, raw, reason, detail, _now()),
        )

    def dlq_items(self, only_unrecovered: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM dlq"
        if only_unrecovered:
            q += " WHERE recovered=0"
        q += " ORDER BY id"
        return self.conn.execute(q).fetchall()

    def mark_dlq_recovered(self, dlq_id: int) -> None:
        self.conn.execute("UPDATE dlq SET recovered=1 WHERE id=?", (dlq_id,))

    def dlq_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT source_kind, COUNT(*) c FROM dlq WHERE recovered=0 GROUP BY source_kind"
        ).fetchall()
        return {r["source_kind"]: r["c"] for r in rows}

    # -- checkpoints -------------------------------------------------------
    def get_checkpoint(self, source_file: str) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT byte_offset, line_no FROM checkpoints WHERE source_file=?",
            (source_file,),
        ).fetchone()
        return (row["byte_offset"], row["line_no"]) if row else (0, 0)

    def set_checkpoint(self, source_file: str, byte_offset: int, line_no: int) -> None:
        self.conn.execute(
            "INSERT INTO checkpoints (source_file, byte_offset, line_no, updated_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(source_file) DO UPDATE SET "
            "byte_offset=excluded.byte_offset, line_no=excluded.line_no, "
            "updated_at=excluded.updated_at",
            (source_file, byte_offset, line_no, _now()),
        )
        self.conn.commit()

    # -- schema drift ------------------------------------------------------
    def record_drift(self, source_kind: str, field: str, kind: str, detail: str = "") -> None:
        # De-dupe: only log a given (source, field, kind) drift once.
        exists = self.conn.execute(
            "SELECT 1 FROM schema_drift WHERE source_kind=? AND field=? AND kind=? LIMIT 1",
            (source_kind, field, kind),
        ).fetchone()
        if exists:
            return
        self.conn.execute(
            "INSERT INTO schema_drift (source_kind, field, kind, detail, ts) VALUES (?,?,?,?,?)",
            (source_kind, field, kind, detail, _now()),
        )

    def drift_items(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM schema_drift ORDER BY id").fetchall()

    # -- queries -----------------------------------------------------------
    def list_machines(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT machine FROM telemetry ORDER BY machine"
        ).fetchall()
        return [r["machine"] for r in rows]

    def query_readings(self, machine: str, start: str, end: str,
                       signal: Optional[str] = None) -> list[TelemetryReading]:
        q = ("SELECT * FROM telemetry WHERE machine=? AND ts>=? AND ts<=?")
        args: list = [machine, start, end]
        if signal:
            q += " AND signal=?"
            args.append(signal)
        q += " ORDER BY ts, signal"
        rows = self.conn.execute(q, args).fetchall()
        return [TelemetryReading(**dict(r)) for r in rows]

    def query_maintenance(self, machine: str, start: Optional[str] = None,
                          end: Optional[str] = None) -> list[MaintenanceRecord]:
        q = "SELECT * FROM maintenance WHERE machine=?"
        args: list = [machine]
        if start:
            q += " AND ts>=?"; args.append(start)
        if end:
            q += " AND ts<=?"; args.append(end)
        q += " ORDER BY ts"
        rows = self.conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["closed"] = bool(d["closed"])
            out.append(MaintenanceRecord(**d))
        return out

    def query_mes(self, machine: str, start: str, end: str) -> list[MesEvent]:
        rows = self.conn.execute(
            "SELECT * FROM mes WHERE machine=? AND ts>=? AND ts<=? ORDER BY ts",
            (machine, start, end),
        ).fetchall()
        return [MesEvent(**dict(r)) for r in rows]

    def signal_coverage(self, machine: str) -> dict[str, dict]:
        """Per-signal min/max ts and count — feeds the reliability score."""
        rows = self.conn.execute(
            "SELECT signal, COUNT(*) c, MIN(ts) mn, MAX(ts) mx "
            "FROM telemetry WHERE machine=? GROUP BY signal",
            (machine,),
        ).fetchall()
        return {r["signal"]: {"count": r["c"], "min_ts": r["mn"], "max_ts": r["mx"]}
                for r in rows}

    # -- incidents ---------------------------------------------------------
    def save_incident(self, report: IncidentReport) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO incidents "
            "(incident_id, asset, window_start, window_end, root_cause_category, "
            " confidence, summary, report_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (report.incident_id, report.asset, report.window_start, report.window_end,
             report.root_cause_category, report.confidence, report.root_cause,
             report.model_dump_json(), report.generated_at or _now()),
        )
        self.conn.commit()

    def get_incident(self, incident_id: str) -> Optional[IncidentReport]:
        row = self.conn.execute(
            "SELECT report_json FROM incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
        return IncidentReport.model_validate_json(row["report_json"]) if row else None

    def list_incidents(self) -> list[IncidentReport]:
        rows = self.conn.execute(
            "SELECT report_json FROM incidents ORDER BY created_at DESC"
        ).fetchall()
        return [IncidentReport.model_validate_json(r["report_json"]) for r in rows]

    def prior_incidents(self, asset: str, before_ts: str) -> list[PriorIncident]:
        """Incidents on the same asset that ended before `before_ts`."""
        rows = self.conn.execute(
            "SELECT incident_id, asset, window_start, window_end, root_cause_category, summary "
            "FROM incidents WHERE asset=? AND window_end < ? ORDER BY window_end",
            (asset, before_ts),
        ).fetchall()
        return [
            PriorIncident(
                incident_id=r["incident_id"], asset=r["asset"],
                root_cause_category=r["root_cause_category"],
                window_start=r["window_start"], window_end=r["window_end"],
                summary=r["summary"] or "",
            )
            for r in rows
        ]

    # -- convenience for UI / stats ---------------------------------------
    def counts(self) -> dict[str, int]:
        c = {}
        for tbl in ("telemetry", "maintenance", "mes", "incidents"):
            c[tbl] = self.conn.execute(f"SELECT COUNT(*) n FROM {tbl}").fetchone()["n"]
        c["dlq"] = self.conn.execute(
            "SELECT COUNT(*) n FROM dlq WHERE recovered=0"
        ).fetchone()["n"]
        return c
