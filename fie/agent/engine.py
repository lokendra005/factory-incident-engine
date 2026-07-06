"""Reasoning engines.

RuleBasedEngine is deterministic and offline — the default. It ships in two
versions:

  * 1.1.0  (prompt "heuristic-v1"): BUGGY. Blames any spindle-temperature rise
           on cooling degradation, ignoring whether coolant flow actually
           dropped. It therefore misclassifies sensor faults and overloads.
  * 1.2.0  (prompt "heuristic-v2"): FIXED. Requires a corroborating coolant-flow
           drop for a cooling diagnosis, and separates overload (load pinned)
           and sensor fault (temp step with no coolant/load change).

The bug is real and documented in docs/failure-model.md; the replay harness
proves 1.2.0 fixes 1.1.0's misses with no regressions.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import Evidence, EvidenceBundle, IncidentReport, TimelineEntry
from .tools import SignalStats, Toolbox

# ---- signature thresholds (deviation from nominal) -----------------------
TEMP_RISE = 25.0          # deg C above baseline
COOLANT_DROP_FRAC = 0.5   # end < 50% of baseline
LOAD_HIGH = 90.0          # % absolute
VIB_RISE = 6.0            # mm/s above baseline
DEFECT_HIGH = 5.0         # % absolute
STEP_JUMP = 40.0          # a single-sample jump this large => step (sensor-like)

RECOMMENDATIONS = {
    "cooling_degradation": [
        "Inspect and reseal the coolant pump; verify flow returns to nominal.",
        "Hold the asset until coolant flow is confirmed >= 24 L/min.",
        "Review the last coolant-pump PM — it was flagged marginal.",
    ],
    "sensor_fault": [
        "Do NOT treat this as a thermal event — coolant flow and load were nominal.",
        "Recalibrate / replace the spindle temperature sensor (T-19).",
        "Re-open the deferred sensor calibration ticket.",
    ],
    "bearing_wear": [
        "Schedule spindle bearing inspection; vibration exceeded threshold.",
        "Reduce feed until inspection to limit further wear.",
        "Compare against the previous bearing-replacement interval.",
    ],
    "tool_wear": [
        "Index/replace the cutting tool; defect rate trended up.",
        "Add an SPC alarm on defect_rate_pct at 5%.",
    ],
    "overload": [
        "Revert the feed-rate override; load was pinned near 100%.",
        "Verify spindle temperature recovers after load normalizes.",
    ],
    "operator_config": [
        "Review the operator config change that preceded the degradation.",
        "Restore the prior parameter set and re-measure.",
    ],
    "no_incident": [
        "No action required; parameters within normal range.",
    ],
    "unknown": [
        "Restore telemetry coverage for this asset before concluding.",
        "Manually inspect the asset for the window in question.",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_timeline(stats: dict, mes: list) -> list[TimelineEntry]:
    """Shared timeline construction — used by the rule engine and the ML engine
    so a diagnosis renders identically regardless of how the category was chosen."""
    timeline: list[TimelineEntry] = []
    interesting = {
        "spindle_temp_c": ("Spindle temperature", "warn"),
        "coolant_flow_lpm": ("Coolant flow", "warn"),
        "vibration_mm_s": ("Vibration", "warn"),
        "defect_rate_pct": ("Defect rate", "warn"),
        "spindle_load_pct": ("Spindle load", "info"),
    }
    for sig, (label, sev) in interesting.items():
        st = stats.get(sig)
        if st and st.n and abs(st.delta) >= 0.25 * (abs(st.baseline) + 1e-6) and st.first_anomaly_ts:
            arrow = "rose" if st.delta > 0 else "fell"
            timeline.append(TimelineEntry(
                ts=st.first_anomaly_ts, signal=sig, severity=sev,
                description=f"{label} {arrow} from {st.baseline} to {st.end}.",
                evidence_ids=st.evidence_ids))
    for e in mes:
        sev = "critical" if e.event == "shutdown" else (
            "warn" if e.event in ("error_code", "config_change") else "info")
        timeline.append(TimelineEntry(
            ts=e.ts, severity=sev,
            description=f"MES {e.event}: {e.detail or e.code}".strip(),
            evidence_ids=[e.id]))
    timeline.sort(key=lambda t: t.ts)
    return timeline


class RuleBasedEngine:
    def __init__(self, version: str = "1.2.0"):
        self.version = version
        # numeric compare so "1.10.0" > "1.2.0" holds if more versions appear
        self.version_tuple = tuple(int(x) for x in version.split("."))
        self.name = f"rule-based/{version}"
        self.prompt_version = "heuristic-v2" if self.version_tuple >= (1, 2, 0) else "heuristic-v1"

    # -- helpers -----------------------------------------------------------
    def _ev(self, bundle: EvidenceBundle, ids: list[str]) -> list[Evidence]:
        out: list[Evidence] = []
        by_id = {r.id: r for r in bundle.readings}
        m_by_id = {m.id: m for m in bundle.maintenance}
        e_by_id = {e.id: e for e in bundle.mes}
        for i in ids:
            if i in by_id:
                r = by_id[i]
                out.append(Evidence(id=i, kind="telemetry",
                                    summary=f"{r.signal}={r.value} @ {r.ts}"))
            elif i in m_by_id:
                m = m_by_id[i]
                out.append(Evidence(id=i, kind="maintenance",
                                    summary=f"{m.kind} {m.component}: {m.note}"))
            elif i in e_by_id:
                e = e_by_id[i]
                out.append(Evidence(id=i, kind="mes",
                                    summary=f"{e.event} {e.code}: {e.detail}"))
        return out

    def _classify(self, s: dict[str, SignalStats], has_config: bool):
        """Return (category, root_cause_text). Version-dependent."""
        temp = s["spindle_temp_c"]
        coolant = s["coolant_flow_lpm"]
        load = s["spindle_load_pct"]
        vib = s["vibration_mm_s"]
        defect = s["defect_rate_pct"]

        temp_rise = temp.delta >= TEMP_RISE
        coolant_drop = coolant.baseline > 0 and coolant.end < COOLANT_DROP_FRAC * coolant.baseline
        load_high = load.end >= LOAD_HIGH
        vib_rise = vib.delta >= VIB_RISE
        defect_high = defect.end >= DEFECT_HIGH
        temp_step = temp.max_jump >= STEP_JUMP

        if self.version_tuple < (1, 2, 0):
            # ---- v1.1: buggy thermal-first heuristic ----
            if temp_rise:
                return "cooling_degradation", (
                    "Spindle temperature rose sharply; attributed to cooling "
                    "degradation.")
            if vib_rise:
                return "bearing_wear", "Vibration exceeded threshold; likely bearing wear."
            if defect_high:
                return "tool_wear", "Defect rate climbed; likely tool wear."
            if has_config:
                return "operator_config", "A config change preceded the change in output."
            return "no_incident", "No abnormal signature detected."

        # ---- v1.2: corroboration-aware heuristic ----
        if load_high and temp_rise:
            return "overload", (
                "Spindle load was sustained near maximum, driving a thermal rise; "
                "coolant flow remained nominal — this is an overload, not a cooling fault.")
        if coolant_drop and temp_rise:
            return "cooling_degradation", (
                "Coolant flow collapsed and spindle temperature rose in lock-step — "
                "cooling degradation.")
        if temp_rise and not coolant_drop and not load_high:
            step = " (step change)" if temp_step else ""
            return "sensor_fault", (
                f"Spindle temperature rose{step} while coolant flow and load stayed "
                "nominal. A real thermal event requires a coolant or load driver; "
                "its absence indicates a temperature-sensor fault, not overheating.")
        if vib_rise and not has_config:
            return "bearing_wear", "Rising vibration with no config change — bearing wear."
        if has_config and (defect_high or vib_rise):
            return "operator_config", (
                "Degradation began immediately after an operator config change.")
        if defect_high and not has_config:
            return "tool_wear", "Defect rate trended up with no other driver — tool wear."
        if not (temp_rise or vib_rise or defect_high or coolant_drop):
            return "no_incident", "All signals within normal range."
        return "unknown", "Signature is ambiguous; insufficient corroboration."

    # -- main --------------------------------------------------------------
    def reconstruct(self, bundle: EvidenceBundle, reliability: float = 1.0):
        tb = Toolbox(bundle)
        stats = {sig: tb.query_telemetry(sig) for sig in
                 ["spindle_temp_c", "coolant_flow_lpm", "spindle_load_pct",
                  "vibration_mm_s", "defect_rate_pct", "throughput_pph"]}
        maint = tb.search_maintenance("")
        mes = tb.mes_events()
        has_config = any(e.event == "config_change" for e in mes)

        category, root_cause = self._classify(stats, has_config)
        similar = tb.find_similar_incidents(category)

        timeline = build_timeline(stats, mes)

        # ---- supporting evidence (grounding) ----
        ev_ids: list[str] = []
        for sig in self._key_signals_for(category):
            ev_ids += stats[sig].evidence_ids
        ev_ids += [m.id for m in maint]
        ev_ids += [e.id for e in mes]
        supporting = self._ev(bundle, list(dict.fromkeys(ev_ids)))

        # ---- confidence ----
        base_conf = {
            "cooling_degradation": 0.82, "overload": 0.80, "bearing_wear": 0.80,
            "tool_wear": 0.78, "operator_config": 0.78, "sensor_fault": 0.75,
            "no_incident": 0.85, "unknown": 0.25,
        }.get(category, 0.4)
        confidence = round(base_conf * max(reliability, 0.05), 3)

        # ---- missing evidence ----
        missing = self._missing_evidence(category, stats, maint)

        report = IncidentReport(
            incident_id="",  # stamped by orchestrator
            asset=bundle.asset, window_start=bundle.window_start,
            window_end=bundle.window_end, root_cause=root_cause,
            root_cause_category=category, confidence=confidence, timeline=timeline,
            supporting_evidence=supporting, missing_evidence=missing,
            recommended_actions=list(RECOMMENDATIONS.get(category, [])),
            similar_incidents=[p.incident_id for p in similar],
            engine=self.name, agent_version=self.name,
            prompt_version=self.prompt_version, generated_at=_now(),
            data_reliability=reliability,
        )
        return report, tb.calls

    def _key_signals_for(self, category: str) -> list[str]:
        return {
            "cooling_degradation": ["coolant_flow_lpm", "spindle_temp_c"],
            "sensor_fault": ["spindle_temp_c", "coolant_flow_lpm"],
            "overload": ["spindle_load_pct", "spindle_temp_c"],
            "bearing_wear": ["vibration_mm_s"],
            "tool_wear": ["defect_rate_pct"],
            "operator_config": ["defect_rate_pct", "vibration_mm_s"],
            "no_incident": [],
            "unknown": [],
        }.get(category, [])

    def _missing_evidence(self, category, stats, maint) -> list[str]:
        out: list[str] = []
        if category == "sensor_fault":
            if not any("sensor" in m.component.lower() for m in maint):
                out.append("Sensor calibration/inspection record to confirm the fault.")
        if category == "cooling_degradation":
            if not any("coolant" in m.component.lower() for m in maint):
                out.append("Recent coolant-pump inspection report.")
        if category == "bearing_wear":
            out.append("Bearing vibration spectrum / FFT for confirmation.")
        return out


# --------------------------------------------------------------------------
# Engine registry / selection
# --------------------------------------------------------------------------

ENGINES = {
    "rule-based/1.1.0": lambda: RuleBasedEngine("1.1.0"),
    "rule-based/1.2.0": lambda: RuleBasedEngine("1.2.0"),
}


def get_engine(name: str | None = None):
    """Resolve an engine by name.

    auto  -> Grok if an xAI key is set, else Claude if an Anthropic key is set,
             else the deterministic rule engine.
    rule / rule-1.1 / rule-based/x.y.z -> deterministic engines.
    grok / claude -> the LLM backend (falls back to rule on any failure).
    ml    -> the trained sklearn classifier engine (falls back to rule if no
             model file / sklearn is present).
    """
    from .. import config
    name = name or config.ENGINE

    if name in ENGINES:
        return ENGINES[name]()
    if name in ("rule", "rule-based"):
        return RuleBasedEngine("1.2.0")
    if name in ("rule-1.1", "1.1"):
        return RuleBasedEngine("1.1.0")

    if name == "ml":
        try:
            from .ml_engine import MLEngine
            return MLEngine()
        except Exception:
            return RuleBasedEngine("1.2.0")

    if name in ("grok", "claude", "auto"):
        try:
            from .llm import ClaudeEngine, GrokEngine, claude_available, grok_available
            if name == "grok":
                eng = GrokEngine()
                return eng if eng.available() else RuleBasedEngine("1.2.0")
            if name == "claude":
                eng = ClaudeEngine()
                return eng if eng.available() else RuleBasedEngine("1.2.0")
            # auto
            if grok_available():
                return GrokEngine()
            if claude_available():
                return ClaudeEngine()
        except Exception:
            pass
        return RuleBasedEngine("1.2.0")

    return RuleBasedEngine("1.2.0")
