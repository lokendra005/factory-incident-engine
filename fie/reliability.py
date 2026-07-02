"""Data-quality reliability score + deployment gate.

Mature engineering judgment, encoded: an agent must not act on data it cannot
trust. We score the evidence available for an asset/window and, below a
threshold, the gate BLOCKS reconstruction — the report comes back with
``blocked=True`` and an explanation instead of a confident-but-baseless answer.

The score is computed from the EvidenceBundle itself (not the store), so it is
identical on the live path and the replay path.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from . import config
from .models import EvidenceBundle


class SourceScore(BaseModel):
    score: float
    detail: str


class ReliabilityReport(BaseModel):
    asset: str
    overall: float
    blocked: bool
    reason: str = ""
    sources: dict[str, SourceScore] = Field(default_factory=dict)
    expected_frames: int = 0
    observed_frames: int = 0
    largest_gap_frames: int = 0
    stale_frames: int = 0


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def assess(bundle: EvidenceBundle, sample_seconds: int = config.SAMPLE_SECONDS) -> ReliabilityReport:
    start, end = _iso(bundle.window_start), _iso(bundle.window_end)
    duration = max((end - start).total_seconds(), 0.0)
    expected = int(duration // sample_seconds) + 1

    ts_sorted = sorted({r.ts for r in bundle.readings})
    observed = len(ts_sorted)
    coverage = min(observed / expected, 1.0) if expected else 0.0

    # largest internal gap, in frames
    largest_gap = 0
    if len(ts_sorted) >= 2:
        prev = _iso(ts_sorted[0])
        for t in ts_sorted[1:]:
            cur = _iso(t)
            gap = int((cur - prev).total_seconds() // sample_seconds) - 1
            largest_gap = max(largest_gap, gap)
            prev = cur

    # staleness: frames between last reading and window end
    stale = 0
    if ts_sorted:
        stale = max(int((end - _iso(ts_sorted[-1])).total_seconds() // sample_seconds), 0)
    else:
        stale = expected

    staleness_factor = 1.0
    if stale > config.STALE_SAMPLES:
        staleness_factor = max(0.0, 1.0 - (stale - config.STALE_SAMPLES) / max(expected, 1))

    tel_score = round(max(0.0, coverage * staleness_factor), 3)
    maint_score = 1.0 if bundle.maintenance else 0.6
    mes_score = 1.0 if bundle.mes else 0.6

    sources = {
        "telemetry": SourceScore(
            score=tel_score,
            detail=(f"{observed}/{expected} frames present "
                    f"(coverage {coverage:.0%}, largest gap {largest_gap} frames, "
                    f"stale {stale} frames)")),
        "maintenance": SourceScore(
            score=maint_score,
            detail=f"{len(bundle.maintenance)} record(s) in scope"),
        "mes": SourceScore(
            score=mes_score,
            detail=f"{len(bundle.mes)} event(s) in scope"),
    }

    # Telemetry gates deployment; the others are context.
    overall = tel_score
    blocked = overall < config.GATE_MIN_SCORE
    reason = ""
    if blocked:
        gap_pct = 1 - coverage
        reason = (f"Telemetry reliability {overall:.0%} is below the "
                  f"{config.GATE_MIN_SCORE:.0%} gate ({gap_pct:.0%} of expected "
                  f"frames missing). Reconstruction blocked to avoid acting on "
                  f"untrustworthy data.")

    return ReliabilityReport(
        asset=bundle.asset, overall=overall, blocked=blocked, reason=reason,
        sources=sources, expected_frames=expected, observed_frames=observed,
        largest_gap_frames=largest_gap, stale_frames=stale,
    )
