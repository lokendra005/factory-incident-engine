"""Feature extraction shared by training and serving.

The same function produces the feature vector at train time (from generated
bundles) and at inference time (from a live bundle), which is what prevents
train/serve skew — a classic, silent source of production ML bugs. If you change
a feature here, both paths change together.
"""
from __future__ import annotations

from ..models import EvidenceBundle
from .tools import Toolbox

SIGNALS = ["spindle_temp_c", "coolant_flow_lpm", "spindle_load_pct",
           "vibration_mm_s", "defect_rate_pct", "throughput_pph"]
_STATS = ["baseline", "end", "delta", "max_jump"]

# Stable, ordered feature names. Order is the contract between train and serve.
FEATURE_NAMES: list[str] = (
    [f"{s}__{stat}" for s in SIGNALS for stat in _STATS]
    + ["has_config_change", "n_error_codes", "n_maintenance", "reliability"]
)


def extract_features(bundle: EvidenceBundle, reliability: float | None = None) -> dict[str, float]:
    tb = Toolbox(bundle)
    stats = {s: tb.query_telemetry(s) for s in SIGNALS}
    mes = tb.mes_events()
    maint = tb.search_maintenance("")

    feats: dict[str, float] = {}
    for s in SIGNALS:
        st = stats[s]
        feats[f"{s}__baseline"] = float(st.baseline)
        feats[f"{s}__end"] = float(st.end)
        feats[f"{s}__delta"] = float(st.delta)
        feats[f"{s}__max_jump"] = float(st.max_jump)
    feats["has_config_change"] = float(any(e.event == "config_change" for e in mes))
    feats["n_error_codes"] = float(sum(1 for e in mes if e.event == "error_code"))
    feats["n_maintenance"] = float(len(maint))
    if reliability is None:
        from ..reliability import assess
        reliability = assess(bundle).overall
    feats["reliability"] = float(reliability)
    return feats


def features_vector(bundle: EvidenceBundle, reliability: float | None = None) -> list[float]:
    feats = extract_features(bundle, reliability)
    return [feats[name] for name in FEATURE_NAMES]
