"""Reconstruction orchestrator.

Flow:  build EvidenceBundle -> assess reliability -> GATE -> engine -> stamp
provenance -> capture RunTrace (snapshotting inputs for replay) -> persist.

If the reliability gate blocks the asset/window we do NOT run the engine: we
return an explicit "unknown / blocked" report rather than a confident guess on
untrustworthy data.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .. import config
from ..models import EvidenceBundle, IncidentReport, RunTrace
from ..reliability import assess
from ..store import Store
from .engine import get_engine


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def incident_id_for(asset: str, window_start: str) -> str:
    return "INC-" + hashlib.sha1(f"{asset}|{window_start}".encode()).hexdigest()[:8]


def _run_id(incident_id: str, engine_name: str) -> str:
    tag = hashlib.sha1(engine_name.encode()).hexdigest()[:6]
    return f"RUN-{incident_id}-{tag}"


def reconstruct(bundle: EvidenceBundle, engine=None, store: Optional[Store] = None,
                save: bool = True) -> RunTrace:
    engine = engine or get_engine()
    rel = assess(bundle)
    incident_id = incident_id_for(bundle.asset, bundle.window_start)

    if rel.blocked:
        report = IncidentReport(
            incident_id=incident_id, asset=bundle.asset,
            window_start=bundle.window_start, window_end=bundle.window_end,
            root_cause="Reconstruction blocked: telemetry too sparse to trust.",
            root_cause_category="unknown", confidence=round(0.2 * rel.overall, 3),
            missing_evidence=[
                rel.reason,
                f"Restore telemetry coverage (only {rel.observed_frames}/"
                f"{rel.expected_frames} frames present; largest gap "
                f"{rel.largest_gap_frames} frames).",
            ],
            recommended_actions=[
                "Restore/backfill telemetry for this asset and window.",
                "Do not action any automated response until coverage recovers.",
            ],
            engine=engine.name, agent_version=engine.name,
            prompt_version=getattr(engine, "prompt_version", ""),
            generated_at=_now(), data_reliability=rel.overall,
            blocked=True, blocked_reason=rel.reason,
        )
        tool_calls = []
    else:
        report, tool_calls = engine.reconstruct(bundle, rel.overall)
        report.incident_id = incident_id
        report.data_reliability = rel.overall
        report.blocked = False

    trace = RunTrace(
        run_id=_run_id(incident_id, engine.name), incident_id=incident_id,
        asset=bundle.asset, window_start=bundle.window_start,
        window_end=bundle.window_end, engine=engine.name,
        agent_version=engine.name, prompt_version=getattr(engine, "prompt_version", ""),
        created_at=_now(), inputs=bundle, tool_calls=tool_calls, report=report,
    )

    if save:
        save_trace(trace)
        if store is not None:
            store.save_incident(report)
    return trace


def reconstruct_from_store(store: Store, asset: str, window_start: str,
                           window_end: str, engine=None,
                           maint_lookback_days: int = 120,
                           persist: bool = True) -> RunTrace:
    since = (datetime.fromisoformat(window_start)
             - timedelta(days=maint_lookback_days)).isoformat()
    bundle = EvidenceBundle(
        asset=asset, window_start=window_start, window_end=window_end,
        readings=store.query_readings(asset, window_start, window_end),
        maintenance=store.query_maintenance(asset, since, window_end),
        mes=store.query_mes(asset, window_start, window_end),
        past_incidents=store.prior_incidents(asset, window_start),
    )
    # persist=False -> a live "what would engine X say?" run for the UI that
    # does not overwrite the canonical stored incident.
    if persist:
        return reconstruct(bundle, engine=engine, store=store)
    return reconstruct(bundle, engine=engine, store=None, save=False)


# --------------------------------------------------------------------------
# Trace persistence (JSON files -> git-friendly + inspectable)
# --------------------------------------------------------------------------

def save_trace(trace: RunTrace) -> Path:
    config.ensure_dirs()
    path = config.RUNS_DIR / f"{trace.run_id}.json"
    path.write_text(trace.model_dump_json(indent=2))
    return path


def load_trace(run_id: str) -> RunTrace:
    path = config.RUNS_DIR / f"{run_id}.json"
    return RunTrace.model_validate_json(path.read_text())


def list_traces() -> list[RunTrace]:
    config.ensure_dirs()
    out = []
    for p in sorted(config.RUNS_DIR.glob("RUN-*.json")):
        try:
            out.append(RunTrace.model_validate_json(p.read_text()))
        except Exception:
            continue
    return out
