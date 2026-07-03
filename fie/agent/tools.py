"""Agent tools over an EvidenceBundle, with call capture for tracing/eval.

Every tool call is recorded (name, args, result count, a sample of result ids)
so the run trace is a faithful, replayable record of what the agent looked at.
The rule-based engine and the Claude engine call the exact same toolbox, so
tool-usage evaluation is meaningful for both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from ..models import EvidenceBundle, MaintenanceRecord, MesEvent, PriorIncident, ToolCall, TelemetryReading


@dataclass
class SignalStats:
    signal: str
    n: int
    baseline: float          # mean of first fifth of the window
    end: float               # mean of last tenth of the window
    delta: float             # end - baseline
    vmax: float
    vmin: float
    max_jump: float          # largest change between consecutive samples
    first_anomaly_ts: str | None
    evidence_ids: list[str]  # ids that best show the signature


class Toolbox:
    def __init__(self, bundle: EvidenceBundle):
        self.bundle = bundle
        self.calls: list[ToolCall] = []

    # -- query_telemetry ---------------------------------------------------
    def query_telemetry(self, signal: str) -> SignalStats:
        rows: list[TelemetryReading] = sorted(
            [r for r in self.bundle.readings if r.signal == signal], key=lambda r: r.ts
        )
        if not rows:
            self.calls.append(ToolCall(name="query_telemetry", args={"signal": signal},
                                       result_count=0, ok=False, note="no readings"))
            return SignalStats(signal, 0, 0, 0, 0, 0, 0, 0, None, [])

        vals = [r.value for r in rows]
        k = max(1, len(rows) // 5)
        j = max(1, len(rows) // 10)
        baseline = mean(vals[:k])
        end = mean(vals[-j:])
        vmax, vmin = max(vals), min(vals)

        max_jump = 0.0
        jump_ts = None
        first_anom = None
        for a, b in zip(rows, rows[1:]):
            d = abs(b.value - a.value)
            if d > max_jump:
                max_jump, jump_ts = d, b.ts
        # first sample that deviates >30% from baseline
        for r in rows:
            if baseline and abs(r.value - baseline) > 0.3 * abs(baseline) + 1e-6:
                first_anom = r.ts
                break

        # evidence: baseline sample, the max-deviation sample, the last sample
        dev_row = max(rows, key=lambda r: abs(r.value - baseline))
        ev_ids = list(dict.fromkeys([rows[0].id, dev_row.id, rows[-1].id]))

        self.calls.append(ToolCall(
            name="query_telemetry", args={"signal": signal},
            result_count=len(rows), result_ids=ev_ids[:3],
            note=f"baseline={baseline:.2f} end={end:.2f} max_jump={max_jump:.2f}"))
        return SignalStats(signal, len(rows), round(baseline, 3), round(end, 3),
                           round(end - baseline, 3), round(vmax, 3), round(vmin, 3),
                           round(max_jump, 3), first_anom or jump_ts, ev_ids)

    # -- search_maintenance ------------------------------------------------
    def search_maintenance(self, keyword: str = "") -> list[MaintenanceRecord]:
        kw = keyword.lower()
        rows = [m for m in self.bundle.maintenance
                if not kw or kw in (m.component + " " + m.note + " " + m.kind).lower()]
        self.calls.append(ToolCall(
            name="search_maintenance", args={"keyword": keyword},
            result_count=len(rows), result_ids=[m.id for m in rows[:5]]))
        return rows

    # -- find_similar_incidents -------------------------------------------
    def find_similar_incidents(self, category: str) -> list[PriorIncident]:
        rows = [p for p in self.bundle.past_incidents if p.root_cause_category == category]
        self.calls.append(ToolCall(
            name="find_similar_incidents", args={"category": category},
            result_count=len(rows), result_ids=[p.incident_id for p in rows[:5]]))
        return rows

    # -- mes events (not a "tool call" but handy) --------------------------
    def mes_events(self) -> list[MesEvent]:
        return sorted(self.bundle.mes, key=lambda e: e.ts)
