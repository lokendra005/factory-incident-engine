"""Optional Claude reasoning backend.

Design goals:
  * Same toolbox + same grounding contract as the rule-based engine, so the
    evaluation harness scores it identically.
  * Never a hard dependency: if the `anthropic` SDK or an API key is missing, or
    any call fails, we fall back to the deterministic engine. The demo, tests,
    and CI never require network access.

The engine hands Claude a compact, pre-computed evidence summary (so token cost
is bounded and the citable ids are fixed up front) and asks for a strict JSON
verdict. Any claim citing an id that is not in the bundle is dropped — the model
cannot invent evidence.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .. import config
from ..models import Evidence, EvidenceBundle, IncidentReport, TimelineEntry
from .engine import RuleBasedEngine, RECOMMENDATIONS
from .tools import Toolbox

_SYSTEM = (
    "You are a manufacturing reliability engineer reconstructing a machine "
    "incident. You are given pre-extracted signal statistics and events. "
    "Diagnose the single most likely root cause. Rules: a thermal (cooling) "
    "diagnosis REQUIRES a corroborating coolant-flow drop; a temperature rise "
    "with nominal coolant and load is a SENSOR FAULT; sustained ~100% load with "
    "a temperature rise is OVERLOAD. If telemetry coverage is poor, return "
    "'unknown' with low confidence. Cite only the evidence ids provided. "
    "Respond with ONLY a JSON object."
)

_SCHEMA_HINT = {
    "root_cause_category": "one of: cooling_degradation|sensor_fault|bearing_wear|"
                           "tool_wear|overload|operator_config|no_incident|unknown",
    "root_cause": "one-sentence explanation",
    "confidence": "0..1 float",
    "supporting_evidence_ids": ["ids drawn ONLY from the provided candidates"],
    "missing_evidence": ["strings"],
}


def claude_available() -> bool:
    if config.ENGINE == "rule":
        return False
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClaudeEngine:
    def __init__(self, model: str | None = None):
        self.model = model or config.CLAUDE_MODEL
        self.name = f"claude/{self.model}"
        self.prompt_version = "claude-json-v1"
        self._fallback = RuleBasedEngine("1.2.0")

    def _summarize(self, tb: Toolbox) -> tuple[dict, list[str]]:
        stats = {sig: tb.query_telemetry(sig) for sig in
                 ["spindle_temp_c", "coolant_flow_lpm", "spindle_load_pct",
                  "vibration_mm_s", "defect_rate_pct", "throughput_pph"]}
        maint = tb.search_maintenance("")
        mes = tb.mes_events()
        candidate_ids: list[str] = []
        for st in stats.values():
            candidate_ids += st.evidence_ids
        candidate_ids += [m.id for m in maint] + [e.id for e in mes]
        summary = {
            "signals": {k: {"baseline": v.baseline, "end": v.end, "delta": v.delta,
                            "max_jump": v.max_jump, "evidence_ids": v.evidence_ids}
                        for k, v in stats.items()},
            "maintenance": [{"id": m.id, "component": m.component, "note": m.note,
                             "closed": m.closed} for m in maint],
            "mes": [{"id": e.id, "event": e.event, "code": e.code, "detail": e.detail}
                    for e in mes],
            "candidate_evidence_ids": list(dict.fromkeys(candidate_ids)),
        }
        return summary, list(dict.fromkeys(candidate_ids))

    def reconstruct(self, bundle: EvidenceBundle, reliability: float = 1.0):
        tb = Toolbox(bundle)
        summary, candidate_ids = self._summarize(tb)
        try:
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=self.model, max_tokens=1024, system=_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({
                    "asset": bundle.asset, "reliability": reliability,
                    "schema": _SCHEMA_HINT, "evidence": summary})}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except Exception:
            # any failure -> deterministic fallback, but keep the tool trace
            report, _calls = self._fallback.reconstruct(bundle, reliability)
            report.engine = f"{self.name} (fell back to {self._fallback.name})"
            return report, tb.calls

        valid = set(candidate_ids)
        cited = [i for i in data.get("supporting_evidence_ids", []) if i in valid]
        supporting = self._resolve(bundle, cited)
        category = data.get("root_cause_category", "unknown")
        report = IncidentReport(
            incident_id="", asset=bundle.asset, window_start=bundle.window_start,
            window_end=bundle.window_end,
            root_cause=data.get("root_cause", "Undetermined"),
            root_cause_category=category,
            confidence=round(float(data.get("confidence", 0.4)) * max(reliability, 0.05), 3),
            timeline=self._timeline(bundle),
            supporting_evidence=supporting,
            missing_evidence=list(data.get("missing_evidence", [])),
            recommended_actions=list(RECOMMENDATIONS.get(category, [])),
            similar_incidents=[p.incident_id for p in tb.find_similar_incidents(category)],
            engine=self.name, agent_version=self.name,
            prompt_version=self.prompt_version, generated_at=_now(),
            data_reliability=reliability,
        )
        return report, tb.calls

    def _resolve(self, bundle: EvidenceBundle, ids: list[str]) -> list[Evidence]:
        return self._fallback._ev(bundle, ids)

    def _timeline(self, bundle: EvidenceBundle) -> list[TimelineEntry]:
        out = [TimelineEntry(ts=e.ts, description=f"MES {e.event}: {e.detail or e.code}",
                             evidence_ids=[e.id],
                             severity="critical" if e.event == "shutdown" else "warn")
               for e in sorted(bundle.mes, key=lambda e: e.ts)]
        return out
