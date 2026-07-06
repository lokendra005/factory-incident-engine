"""LLM reasoning backends (Claude + Grok) over a shared base.

Both providers get the *same* pre-computed evidence summary, the same strict-JSON
contract, and the same grounding guard (a cited id that isn't in the bundle is
dropped — the model cannot invent evidence). Only the transport differs:

  * ClaudeEngine -> Anthropic SDK (optional dependency)
  * GrokEngine   -> xAI's OpenAI-compatible REST API via httpx (no SDK)

Any failure (missing key, network error, bad JSON) falls back to the
deterministic rule-based engine, so nothing here is ever load-bearing for the
demo, the tests, or CI.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .. import config
from ..models import Evidence, EvidenceBundle, IncidentReport, TimelineEntry
from .engine import RECOMMENDATIONS, RuleBasedEngine
from .tools import Toolbox

_SYSTEM = (
    "You are a manufacturing reliability engineer reconstructing a machine "
    "incident from pre-extracted signal statistics and events. Diagnose the "
    "single most likely root cause. Rules: a cooling diagnosis REQUIRES a "
    "corroborating coolant-flow drop; a temperature rise with nominal coolant "
    "and load is a SENSOR FAULT; sustained ~100% load with a temperature rise "
    "is OVERLOAD. If telemetry coverage is poor, return 'unknown' with low "
    "confidence. Cite only the provided evidence ids. Respond with ONLY a JSON "
    "object matching the schema."
)

_SCHEMA_HINT = {
    "root_cause_category": ("one of: cooling_degradation|sensor_fault|bearing_wear|"
                            "tool_wear|overload|operator_config|no_incident|unknown"),
    "root_cause": "one-sentence explanation",
    "confidence": "0..1 float",
    "supporting_evidence_ids": ["ids drawn ONLY from candidate_evidence_ids"],
    "missing_evidence": ["strings"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LLMEngine:
    """Base class: turns a bundle into a summary, calls `_complete`, grounds it."""
    name = "llm"
    prompt_version = "llm-json-v1"

    def __init__(self):
        self._fallback = RuleBasedEngine("1.2.0")

    # subclasses implement the transport
    def _complete(self, system: str, user_json: str) -> str:
        raise NotImplementedError

    def available(self) -> bool:
        return False

    # -- shared pipeline ---------------------------------------------------
    def _summarize(self, tb: Toolbox):
        stats = {sig: tb.query_telemetry(sig) for sig in
                 ["spindle_temp_c", "coolant_flow_lpm", "spindle_load_pct",
                  "vibration_mm_s", "defect_rate_pct", "throughput_pph"]}
        maint = tb.search_maintenance("")
        mes = tb.mes_events()
        candidate_ids = []
        for st in stats.values():
            candidate_ids += st.evidence_ids
        candidate_ids += [m.id for m in maint] + [e.id for e in mes]
        candidate_ids = list(dict.fromkeys(candidate_ids))
        summary = {
            "signals": {k: {"baseline": v.baseline, "end": v.end, "delta": v.delta,
                            "max_jump": v.max_jump, "evidence_ids": v.evidence_ids}
                        for k, v in stats.items()},
            "maintenance": [{"id": m.id, "component": m.component, "note": m.note,
                             "closed": m.closed} for m in maint],
            "mes": [{"id": e.id, "event": e.event, "code": e.code, "detail": e.detail}
                    for e in mes],
            "candidate_evidence_ids": candidate_ids,
        }
        return summary, candidate_ids

    def reconstruct(self, bundle: EvidenceBundle, reliability: float = 1.0):
        tb = Toolbox(bundle)
        summary, candidate_ids = self._summarize(tb)
        user = json.dumps({"asset": bundle.asset, "reliability": reliability,
                           "schema": _SCHEMA_HINT, "evidence": summary})
        try:
            text = self._complete(_SYSTEM, user)
            data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except Exception:
            report, _ = self._fallback.reconstruct(bundle, reliability)
            report.engine = f"{self.name} (fell back to {self._fallback.name})"
            report.agent_version = report.engine
            return report, tb.calls

        valid = set(candidate_ids)
        cited = [i for i in data.get("supporting_evidence_ids", []) if i in valid]
        category = data.get("root_cause_category", "unknown")
        report = IncidentReport(
            incident_id="", asset=bundle.asset, window_start=bundle.window_start,
            window_end=bundle.window_end,
            root_cause=data.get("root_cause", "Undetermined"),
            root_cause_category=category,
            confidence=round(float(data.get("confidence", 0.4)) * max(reliability, 0.05), 3),
            timeline=self._timeline(bundle),
            supporting_evidence=self._fallback._ev(bundle, cited),
            missing_evidence=list(data.get("missing_evidence", [])),
            recommended_actions=list(RECOMMENDATIONS.get(category, [])),
            similar_incidents=[p.incident_id for p in tb.find_similar_incidents(category)],
            engine=self.name, agent_version=self.name,
            prompt_version=self.prompt_version, generated_at=_now(),
            data_reliability=reliability,
        )
        return report, tb.calls

    def _timeline(self, bundle: EvidenceBundle):
        return [TimelineEntry(ts=e.ts, description=f"MES {e.event}: {e.detail or e.code}",
                              evidence_ids=[e.id],
                              severity="critical" if e.event == "shutdown" else "warn")
                for e in sorted(bundle.mes, key=lambda e: e.ts)]


# --------------------------------------------------------------------------
# Claude
# --------------------------------------------------------------------------

class ClaudeEngine(LLMEngine):
    def __init__(self, model: str | None = None):
        super().__init__()
        self.model = model or config.CLAUDE_MODEL
        self.name = f"claude/{self.model}"
        self.prompt_version = "claude-json-v1"

    def available(self) -> bool:
        return claude_available()

    def _complete(self, system: str, user_json: str) -> str:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=self.model, max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user_json}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


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


# --------------------------------------------------------------------------
# Grok (xAI) — OpenAI-compatible REST, called with httpx (no SDK required)
# --------------------------------------------------------------------------

class GrokEngine(LLMEngine):
    def __init__(self, model: str | None = None):
        super().__init__()
        self.model = model or config.GROK_MODEL
        self.name = f"grok/{self.model}"
        self.prompt_version = "grok-json-v1"

    def available(self) -> bool:
        return grok_available()

    def _api_key(self) -> str | None:
        return os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")

    def _complete(self, system: str, user_json: str) -> str:
        import httpx
        resp = httpx.post(
            f"{config.GROK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key()}",
                     "Content-Type": "application/json"},
            json={
                "model": self.model,
                "temperature": 0,                      # determinism-friendly
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_json},
                ],
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def grok_available() -> bool:
    if config.ENGINE == "rule":
        return False
    if not (os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")):
        return False
    try:
        import httpx  # noqa: F401
        return True
    except Exception:
        return False
