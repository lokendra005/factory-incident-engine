"""The ingestion pipeline: read -> validate -> normalize -> store.

Crash safety: we checkpoint the last committed line number per file. On restart
we skip already-committed lines, and because every canonical upsert is
idempotent (keyed on a deterministic id), any line that *was* processed but not
yet checkpointed is harmlessly re-applied. Net effect: exactly-once.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config
from ..store import Store
from .validate import VALIDATORS


@dataclass
class IngestStats:
    source_file: str = ""
    read: int = 0
    inserted: int = 0
    duplicate: int = 0
    conflict: int = 0
    out_of_order: int = 0
    resumed_from_line: int = 0
    dlq: dict[str, int] = field(default_factory=dict)
    drift: int = 0

    def _dlq(self, reason: str) -> None:
        # collapse "missing_field:machine" etc. to the family for readable stats
        key = reason.split(":")[0]
        self.dlq[key] = self.dlq.get(key, 0) + 1

    @property
    def dlq_total(self) -> int:
        return sum(self.dlq.values())

    def as_dict(self) -> dict:
        return {
            "source_file": self.source_file, "read": self.read,
            "inserted": self.inserted, "duplicate": self.duplicate,
            "conflict": self.conflict, "out_of_order": self.out_of_order,
            "resumed_from_line": self.resumed_from_line,
            "dlq": dict(self.dlq), "dlq_total": self.dlq_total, "drift": self.drift,
        }


def _source_kind(path: Path) -> str:
    stem = path.stem.lower()
    for k in VALIDATORS:
        if k in stem:
            return k
    return "telemetry"


def ingest_file(store: Store, path: Path, source_kind: Optional[str] = None,
                resume: bool = True) -> IngestStats:
    path = Path(path)
    kind_hint = source_kind or _source_kind(path)
    stats = IngestStats(source_file=path.name)

    start_line = 0
    if resume:
        _, start_line = store.get_checkpoint(path.name)
        stats.resumed_from_line = start_line

    max_ts_seen = ""
    committed = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            if line_no <= start_line:      # already committed in a prior run
                continue
            raw = line.rstrip("\n")
            if not raw.strip():
                committed = line_no
                continue
            stats.read += 1

            # 1) parse
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                store.add_dlq(kind_hint, raw, "malformed_json",
                              source_file=path.name, line_no=line_no)
                stats._dlq("malformed_json")
                committed = line_no
                continue
            if not isinstance(rec, dict):
                store.add_dlq(kind_hint, raw, "not_an_object",
                              source_file=path.name, line_no=line_no)
                stats._dlq("not_an_object")
                committed = line_no
                continue

            # 2) route + validate. The validator must never crash the run — any
            # unexpected error (e.g. a model-construction failure on a weirdly
            # typed field) dead-letters the single offending line and moves on.
            kind = rec.get("kind", kind_hint)
            validator = VALIDATORS.get(kind, VALIDATORS[kind_hint])
            try:
                model, reason, drift = validator(rec)
            except Exception as exc:  # noqa: BLE001 - defensive by design
                store.add_dlq(kind_hint, raw, "validation_error",
                              detail=str(exc)[:200], source_file=path.name,
                              line_no=line_no)
                stats._dlq("validation_error")
                committed = line_no
                continue
            for fld, dkind, detail in drift:
                store.record_drift(kind_hint, fld, dkind, detail)
                stats.drift += 1

            if model is None:
                store.add_dlq(kind_hint, raw, reason,
                              source_file=path.name, line_no=line_no)
                stats._dlq(reason)
                committed = line_no
                continue

            # 3) out-of-order detection (informational; storage is ts-agnostic)
            if model.ts < max_ts_seen:
                stats.out_of_order += 1
            else:
                max_ts_seen = model.ts

            # 4) idempotent upsert
            if kind_hint == "telemetry" or rec.get("kind") == "telemetry":
                status = store.upsert_reading(model)
            elif kind == "maintenance":
                status = store.upsert_maintenance(model)
            else:
                status = store.upsert_mes(model)

            if status == "inserted":
                stats.inserted += 1
            elif status == "duplicate":
                stats.duplicate += 1
            elif status == "conflict":
                stats.conflict += 1
                store.add_dlq(kind_hint, raw, "idempotency_conflict",
                              detail="same id, different payload; kept first",
                              source_file=path.name, line_no=line_no)
                stats._dlq("idempotency_conflict")

            committed = line_no
            if committed % config.CHECKPOINT_EVERY == 0:
                store.conn.commit()
                # resume is line-based; byte offset is advisory (0 mid-stream to
                # avoid fh.tell(), which Python disables during line iteration).
                store.set_checkpoint(path.name, 0, committed)

    store.conn.commit()
    if committed:
        store.set_checkpoint(path.name, path.stat().st_size, committed)
    return stats


def ingest_all(store: Store, raw_dir: Optional[Path] = None,
               resume: bool = True) -> dict:
    raw_dir = Path(raw_dir or config.RAW_DIR)
    out: dict[str, dict] = {}
    for name in ("maintenance.jsonl", "mes.jsonl", "telemetry.jsonl"):
        p = raw_dir / name
        if p.exists():
            out[name] = ingest_file(store, p, resume=resume).as_dict()
    return out


# --------------------------------------------------------------------------
# DLQ recovery: after fixing the upstream/mapping, re-drive dead-lettered rows.
# --------------------------------------------------------------------------

# A field remap that repairs a *known* upstream mistake — the batch that renamed
# `value` to `reading_c`. This is exactly the "fix, then replay the DLQ" loop.
DEFAULT_REMAP = {"reading_c": "value"}


def recover_dlq(store: Store, remap: Optional[dict[str, str]] = None) -> dict:
    remap = DEFAULT_REMAP if remap is None else remap
    recovered = 0
    still_bad = 0
    for row in store.dlq_items(only_unrecovered=True):
        try:
            rec = json.loads(row["raw"])
        except (json.JSONDecodeError, ValueError):
            still_bad += 1
            continue
        if not isinstance(rec, dict):
            still_bad += 1
            continue
        for src, dst in remap.items():
            if src in rec and dst not in rec:
                rec[dst] = rec.pop(src)
        kind = rec.get("kind", row["source_kind"])
        validator = VALIDATORS.get(kind, VALIDATORS.get(row["source_kind"], VALIDATORS["telemetry"]))
        try:
            model, reason, _ = validator(rec)
        except Exception:  # noqa: BLE001 - an un-remappable row stays dead-lettered
            still_bad += 1
            continue
        if model is None:
            still_bad += 1
            continue
        if kind == "maintenance":
            store.upsert_maintenance(model)
        elif kind == "mes":
            store.upsert_mes(model)
        else:
            store.upsert_reading(model)
        store.mark_dlq_recovered(row["id"])
        recovered += 1
    store.conn.commit()
    return {"recovered": recovered, "still_dead_lettered": still_bad}
