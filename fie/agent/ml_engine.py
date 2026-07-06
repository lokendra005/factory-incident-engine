"""ML reasoning engine: a trained classifier predicts the root-cause category;
the report scaffolding (timeline, grounded evidence, recommendations) is shared
with the rule engine so its output is a drop-in for eval and replay.

Loads the latest artifact from data/models. If none exists (or a feature
contract mismatch is detected), construction raises and `get_engine('ml')` falls
back to the rule engine.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import EvidenceBundle, IncidentReport
from .engine import RECOMMENDATIONS, RuleBasedEngine, build_timeline
from .features import FEATURE_NAMES, extract_features
from .tools import Toolbox


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MLEngine:
    def __init__(self, model: dict | None = None):
        from ..ml.train import load_latest
        self.model = model or load_latest()
        if self.model["feature_names"] != list(FEATURE_NAMES):
            raise ValueError("feature contract mismatch between model and code "
                             "(train/serve skew) — retrain with `fie train`")
        self.pipeline = self.model["pipeline"]
        self.version = self.model["version"]
        self.name = f"ml/{self.version}"
        self.prompt_version = "ml-rf-v1"
        self._rb = RuleBasedEngine("1.2.0")  # reused for grounding helpers

    def reconstruct(self, bundle: EvidenceBundle, reliability: float = 1.0):
        tb = Toolbox(bundle)
        # record tool calls (so tool-usage evaluation is meaningful for ML too)
        stats = {s: tb.query_telemetry(s) for s in
                 ["spindle_temp_c", "coolant_flow_lpm", "spindle_load_pct",
                  "vibration_mm_s", "defect_rate_pct", "throughput_pph"]}
        maint = tb.search_maintenance("")
        mes = tb.mes_events()

        feats = extract_features(bundle, reliability)
        vec = [feats[name] for name in FEATURE_NAMES]
        proba = self.pipeline.predict_proba([vec])[0]
        classes = list(self.pipeline.named_steps["rf"].classes_)
        idx = max(range(len(proba)), key=lambda i: proba[i])
        category = classes[idx]
        p = float(proba[idx])

        ev_ids = []
        for sig in self._rb._key_signals_for(category):
            ev_ids += stats[sig].evidence_ids
        ev_ids += [m.id for m in maint] + [e.id for e in mes]
        supporting = self._rb._ev(bundle, list(dict.fromkeys(ev_ids)))

        report = IncidentReport(
            incident_id="", asset=bundle.asset, window_start=bundle.window_start,
            window_end=bundle.window_end,
            root_cause=f"{category.replace('_', ' ')} — predicted by RandomForest "
                       f"classifier (p={p:.2f}).",
            root_cause_category=category,
            confidence=round(p * max(reliability, 0.05), 3),
            timeline=build_timeline(stats, mes),
            supporting_evidence=supporting,
            missing_evidence=self._rb._missing_evidence(category, stats, maint),
            recommended_actions=list(RECOMMENDATIONS.get(category, [])),
            similar_incidents=[pi.incident_id for pi in tb.find_similar_incidents(category)],
            engine=self.name, agent_version=self.name,
            prompt_version=self.prompt_version, generated_at=_now(),
            data_reliability=reliability,
        )
        return report, tb.calls
