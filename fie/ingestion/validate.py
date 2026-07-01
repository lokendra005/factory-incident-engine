"""Validation + normalization of raw records into canonical models.

Each ``validate_*`` returns ``(model | None, reason, drift)``:
  * model is None  -> the record is dead-lettered with ``reason``
  * drift is a list of (field, kind, detail) schema-drift observations to log

The bounds and known-signal set come from config, so the simulator (which
decides what counts as "impossible") and the validator agree by construction.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime

from .. import config
from ..models import MaintenanceRecord, MesEvent, TelemetryReading

_TS_MIN = datetime.fromisoformat(config.TS_MIN_ISO)
_TS_MAX = datetime.fromisoformat(config.TS_MAX_ISO)

DriftEvents = list[tuple[str, str, str]]

_TEL_CORE = {"machine", "ts", "signal", "value"}
_TEL_KNOWN = _TEL_CORE | {"kind", "source", "unit"}


def _rid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _parse_ts(raw) -> tuple[str | None, str]:
    if not isinstance(raw, str):
        return None, "ts_not_string"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None, "ts_unparseable"
    if dt.tzinfo is None:
        return None, "ts_naive_no_timezone"
    if dt < _TS_MIN:
        return None, "ts_before_horizon"
    if dt > _TS_MAX:
        return None, "future_timestamp"
    # normalize to canonical UTC iso
    return dt.isoformat(), ""


def validate_telemetry(rec: dict) -> tuple[TelemetryReading | None, str, DriftEvents]:
    drift: DriftEvents = []

    # schema drift: unexpected fields (tolerated) and the renamed-value batch.
    for k in rec:
        if k not in _TEL_KNOWN:
            drift.append((k, "new_field", f"unexpected field '{k}'"))
    if "value" not in rec and "reading_c" in rec:
        drift.append(("value", "missing_field", "value renamed to 'reading_c'"))
        return None, "schema_missing_value", drift

    machine = rec.get("machine")
    signal = rec.get("signal")
    if not machine or not isinstance(machine, str):
        return None, "missing_field:machine", drift
    if not signal or not isinstance(signal, str):
        return None, "missing_field:signal", drift
    if "value" not in rec:
        return None, "missing_field:value", drift

    ts, err = _parse_ts(rec.get("ts"))
    if err:
        return None, err, drift

    value = rec.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "value_not_numeric", drift
    if not math.isfinite(value):
        return None, "value_not_finite", drift

    if signal not in config.SIGNAL_BOUNDS:
        return None, f"unknown_signal:{signal}", drift
    lo, hi = config.SIGNAL_BOUNDS[signal]
    if not (lo <= value <= hi):
        return None, "out_of_bounds", drift

    reading = TelemetryReading(
        id=_rid(machine, ts, signal), machine=machine, ts=ts,
        signal=signal, value=float(value),
    )
    return reading, "", drift


def validate_maintenance(rec: dict) -> tuple[MaintenanceRecord | None, str, DriftEvents]:
    machine = rec.get("machine")
    kind = rec.get("kind_of") or rec.get("maint_kind")
    component = rec.get("component")
    # identity fields must be strings; wrong types are data corruption, not a
    # model bug -> reject cleanly rather than letting model construction raise.
    if not isinstance(machine, str) or not machine:
        return None, "missing_field:machine", []
    if not isinstance(kind, str) or not kind:
        return None, "missing_field:kind", []
    if not isinstance(component, str) or not component:
        return None, "missing_field:component", []
    ts, err = _parse_ts(rec.get("ts"))
    if err:
        return None, err, []
    valid_kinds = {"inspection", "repair", "replace", "lubrication", "calibration"}
    if kind not in valid_kinds:
        return None, f"unknown_kind:{kind}", []
    rec_m = MaintenanceRecord(
        id=_rid(machine, ts, component, kind), machine=machine, ts=ts, kind=kind,
        component=component, note=str(rec.get("note", "")),
        closed=bool(rec.get("closed", True)), technician=str(rec.get("technician", "")),
    )
    return rec_m, "", []


def validate_mes(rec: dict) -> tuple[MesEvent | None, str, DriftEvents]:
    machine = rec.get("machine")
    event = rec.get("event")
    if not isinstance(machine, str) or not machine:
        return None, "missing_field:machine", []
    if not isinstance(event, str) or not event:
        return None, "missing_field:event", []
    ts, err = _parse_ts(rec.get("ts"))
    if err:
        return None, err, []
    valid = {"startup", "shutdown", "config_change", "error_code",
             "state_change", "order_start", "order_complete"}
    if event not in valid:
        return None, f"unknown_event:{event}", []
    e = MesEvent(
        id=_rid(machine, ts, event, str(rec.get("code", ""))), machine=machine, ts=ts,
        event=event, detail=str(rec.get("detail", "")), code=str(rec.get("code", "")),
    )
    return e, "", []


VALIDATORS = {
    "telemetry": validate_telemetry,
    "maintenance": validate_maintenance,
    "mes": validate_mes,
}
