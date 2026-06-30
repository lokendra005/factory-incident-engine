"""Deterministic generation of evidence bundles and messy raw feeds.

Two entry points:
  * build_bundle(scenario)  -> a clean, in-memory EvidenceBundle + labels
                               (used by the evaluation harness; no store, fully
                               reproducible from the scenario alone).
  * write_raw_feed(...)     -> writes messy JSONL "off the wire" for the full
                               ingest->store->reconstruct demo path.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config
from ..models import (
    EvidenceBundle,
    MaintenanceRecord,
    MesEvent,
    PriorIncident,
    TelemetryReading,
)
from .scenarios import Scenario

BASE = datetime(2026, 6, 29, 8, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _rid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _scenario_base(sc: Scenario) -> datetime:
    """Stable, collision-free start time per scenario (no wall-clock)."""
    h = int(hashlib.sha1(sc.key.encode()).hexdigest(), 16) % 400
    return BASE + timedelta(hours=h)


def _signal_value(sc: Scenario, signal: str, minute: int, rng: random.Random) -> float:
    base = config.NOMINAL[signal]
    eff = next((e for e in sc.effects if e.signal == signal), None)
    scale = max(abs(base) * 0.015, 0.05)
    noise = rng.gauss(0.0, scale)
    if eff is None or eff.mode == "flat":
        v = base
    elif eff.mode == "linear":
        frac = minute / max(sc.duration_min, 1)
        v = base + (eff.target - base) * frac
    elif eff.mode == "step_at":
        v = eff.target if minute >= eff.at_min else base
    elif eff.mode == "spike_at":
        v = eff.target if abs(minute - eff.at_min) <= 1 else base
    else:
        v = base
    lo, hi = config.SIGNAL_BOUNDS[signal]
    return round(min(max(v + noise, lo), hi), 3)


def _in_gap(sc: Scenario, minute: int) -> bool:
    return sc.gap_min is not None and sc.gap_min[0] <= minute <= sc.gap_min[1]


# --------------------------------------------------------------------------
# Clean bundle (for evaluation / the pure engine path)
# --------------------------------------------------------------------------

def build_bundle(sc: Scenario, base: datetime | None = None,
                 with_priors: bool = True) -> tuple[EvidenceBundle, dict]:
    base = base or _scenario_base(sc)
    rng = random.Random(f"bundle::{sc.key}")
    signals = list(config.NOMINAL.keys())

    readings: list[TelemetryReading] = []
    observed = 0
    expected = 0
    for minute in range(sc.duration_min + 1):
        expected += 1
        if _in_gap(sc, minute):
            continue
        observed += 1
        ts = _iso(base + timedelta(minutes=minute))
        for sig in signals:
            val = _signal_value(sc, sig, minute, rng)
            readings.append(TelemetryReading(
                id=_rid(sc.asset, ts, sig), machine=sc.asset, ts=ts, signal=sig, value=val,
            ))

    maintenance: list[MaintenanceRecord] = []
    for m in sc.maintenance:
        ts = _iso(base + timedelta(minutes=m.offset_min))
        maintenance.append(MaintenanceRecord(
            id=_rid(sc.asset, ts, m.component, m.kind), machine=sc.asset, ts=ts,
            kind=m.kind, component=m.component, note=m.note, closed=m.closed,
            technician=m.technician,
        ))

    mes: list[MesEvent] = []
    for e in sc.mes:
        ts = _iso(base + timedelta(minutes=e.at_min))
        mes.append(MesEvent(
            id=_rid(sc.asset, ts, e.event, e.code), machine=sc.asset, ts=ts,
            event=e.event, detail=e.detail, code=e.code,
        ))

    past_incidents: list[PriorIncident] = []
    if with_priors:
        prior_ts = _iso(base - timedelta(days=9))
        past_incidents.append(PriorIncident(
            incident_id=f"PAST-{_rid(sc.key,'same')}", asset=sc.asset,
            root_cause_category=sc.category,
            window_start=prior_ts, window_end=prior_ts,
            summary=f"Earlier {sc.category} event on {sc.asset}.",
        ))

    reliability = {
        "telemetry": round(observed / max(expected, 1), 3),
        "maintenance": 1.0 if maintenance else 0.5,
        "mes": 1.0 if mes else 0.5,
    }

    window_start = _iso(base)
    window_end = _iso(base + timedelta(minutes=sc.duration_min))
    bundle = EvidenceBundle(
        asset=sc.asset, window_start=window_start, window_end=window_end,
        readings=readings, maintenance=maintenance, mes=mes,
        past_incidents=past_incidents, reliability=reliability,
    )
    labels = {
        "key": sc.key,
        "expected_category": sc.expected_category,
        "expected_root_cause_kw": list(sc.expected_root_cause_kw),
        "key_signals": list(sc.key_signals),
        "expects_missing_evidence": sc.expects_missing_evidence,
        "asset": sc.asset,
        "window_start": window_start,
        "window_end": window_end,
        "title": sc.title,
        "notes": sc.notes,
    }
    return bundle, labels


# --------------------------------------------------------------------------
# Messy raw feed (for the ingest->store demo path)
# --------------------------------------------------------------------------

def _bundle_to_raw(sc: Scenario, base: datetime, rng: random.Random) -> dict[str, list]:
    """Emit raw (untyped, id-less) records as a real feed would."""
    tel, maint, mes = [], [], []
    signals = list(config.NOMINAL.keys())
    for minute in range(sc.duration_min + 1):
        if _in_gap(sc, minute):
            continue
        ts = _iso(base + timedelta(minutes=minute))
        for sig in signals:
            tel.append({"kind": "telemetry", "machine": sc.asset, "ts": ts,
                        "signal": sig, "value": _signal_value(sc, sig, minute, rng)})
    for m in sc.maintenance:
        ts = _iso(base + timedelta(minutes=m.offset_min))
        maint.append({"kind": "maintenance", "machine": sc.asset, "ts": ts,
                      "kind_of": m.kind, "component": m.component, "note": m.note,
                      "closed": m.closed, "technician": m.technician})
    for e in sc.mes:
        ts = _iso(base + timedelta(minutes=e.at_min))
        mes.append({"kind": "mes", "machine": sc.asset, "ts": ts,
                    "event": e.event, "detail": e.detail, "code": e.code})
    return {"telemetry": tel, "maintenance": maint, "mes": mes}


def write_raw_feed(scenarios: list[Scenario], out_dir: Path | None = None,
                   seed: int = 7) -> dict:
    """Write messy JSONL feeds. Returns a manifest incl. injected-defect counts
    and the (asset, window) of each incident so the demo can reconstruct them."""
    out_dir = Path(out_dir or config.RAW_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    tel_all, maint_all, mes_all = [], [], []
    windows = []
    for idx, sc in enumerate(scenarios):
        base = BASE + timedelta(hours=6 * idx)
        raw = _bundle_to_raw(sc, base, rng)
        tel_all.extend(raw["telemetry"])
        maint_all.extend(raw["maintenance"])
        mes_all.extend(raw["mes"])
        windows.append({
            "key": sc.key, "asset": sc.asset,
            "window_start": _iso(base),
            "window_end": _iso(base + timedelta(minutes=sc.duration_min)),
            "expected_category": sc.expected_category,
            "title": sc.title,
        })

    injected = {"duplicate": 0, "out_of_order": 0, "impossible": 0,
                "malformed": 0, "missing_field": 0, "future_ts": 0,
                "drift_new_field": 0, "drift_missing_value": 0}

    # ---- inject mess into the telemetry stream (the noisy one) ----
    lines: list[str] = []
    n = len(tel_all)
    for i, rec in enumerate(tel_all):
        # schema drift: from ~60% onward, add a new 'unit' field (must be tolerated)
        if i > n * 0.6:
            rec = {**rec, "unit": "metric"}
            injected["drift_new_field"] += 1
        # a contiguous bad batch renames value->reading_c (missing 'value' -> DLQ)
        if int(n * 0.80) <= i < int(n * 0.80) + 12:
            rec = {k: v for k, v in rec.items() if k != "value"}
            rec["reading_c"] = tel_all[i]["value"]
            injected["drift_missing_value"] += 1
        lines.append(json.dumps(rec))

        # duplicates (~2%): exact re-emit -> idempotency must dedupe
        if rng.random() < 0.02:
            lines.append(json.dumps(rec))
            injected["duplicate"] += 1
        # impossible values (~1%): out of physical bounds -> DLQ
        if rng.random() < 0.01:
            bad = {**rec}
            bad.pop("reading_c", None)
            bad["value"] = 9999.0 if rng.random() < 0.5 else -50.0
            lines.append(json.dumps(bad))
            injected["impossible"] += 1
        # missing required field (~0.6%) -> DLQ
        if rng.random() < 0.006:
            bad = {k: v for k, v in rec.items() if k != "signal"}
            lines.append(json.dumps(bad))
            injected["missing_field"] += 1
        # future timestamp (~0.4%) -> DLQ
        if rng.random() < 0.004:
            bad = {**rec, "ts": _iso(BASE + timedelta(days=3650))}
            lines.append(json.dumps(bad))
            injected["future_ts"] += 1
        # malformed json (~0.4%) -> DLQ
        if rng.random() < 0.004:
            lines.append('{"kind":"telemetry","machine":"CNC-17","ts": broken,,}')
            injected["malformed"] += 1

    # out-of-order: swap ~1.5% of adjacent line pairs (ingestion is ts-based, so
    # this must NOT corrupt results; we only count that it was handled).
    for i in range(len(lines) - 1):
        if rng.random() < 0.015:
            lines[i], lines[i + 1] = lines[i + 1], lines[i]
            injected["out_of_order"] += 1

    (out_dir / "telemetry.jsonl").write_text("\n".join(lines) + "\n")
    (out_dir / "maintenance.jsonl").write_text(
        "\n".join(json.dumps(r) for r in maint_all) + "\n")
    (out_dir / "mes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in mes_all) + "\n")

    manifest = {
        "telemetry_lines": len(lines),
        "maintenance_lines": len(maint_all),
        "mes_lines": len(mes_all),
        "injected": injected,
        "windows": windows,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
