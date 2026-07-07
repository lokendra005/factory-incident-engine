"""Loaders for real / benchmark datasets (Kaggle etc.).

These let `fie train` learn from a downloaded dataset instead of the synthetic
generator, so the ML training pipeline is demonstrably not tied to made-up data.

Important honesty note: an externally-trained model lives in its OWN feature and
label space (e.g. AI4I's torque/rpm/temperatures, milling-machine failure modes).
It is a *separate track* from the incident-reconstruction MLEngine, which serves
over the project's six-signal EvidenceBundle. We save external models under an
`ext-*` prefix precisely so they never get loaded by the reconstruction engine
(which globs `ml-*`), avoiding train/serve skew. The value here is proving the
training pipeline generalizes to a real dataset — not pretending a milling model
can diagnose your CNC bundles.
"""
from __future__ import annotations

import math
from pathlib import Path

# AI4I 2020 failure-mode column -> human-readable class label
_AI4I_MODES = {
    "TWF": "tool_wear",
    "HDF": "heat_dissipation",
    "PWF": "power_failure",
    "OSF": "overstrain",
    "RNF": "random_failure",
}
# priority when more than one mode is flagged on a row
_AI4I_PRIORITY = ["HDF", "PWF", "OSF", "TWF", "RNF"]

AI4I_FEATURES = [
    "type_code", "air_temp_k", "process_temp_k", "temp_diff_k",
    "rot_speed_rpm", "torque_nm", "power_w", "tool_wear_min", "overstrain",
]


def _find_col(cols, *candidates) -> str:
    """Resolve a column name across the many AI4I mirror spellings."""
    norm = {c.lower().strip().replace(" ", "").replace("_", "").replace("[", "")
            .replace("]", ""): c for c in cols}
    for cand in candidates:
        key = cand.lower().strip().replace(" ", "").replace("_", "").replace(
            "[", "").replace("]", "")
        if key in norm:
            return norm[key]
    raise KeyError(f"none of {candidates} found in columns {list(cols)}")


def load_ai4i(csv_path: str | Path, failures_only: bool = False):
    """Load the AI4I 2020 Predictive Maintenance CSV.

    Returns (X, y, feature_names). Builds a few physics-derived features
    (temp difference, mechanical power, overstrain proxy) that mirror the
    dataset's documented failure drivers.
    """
    import pandas as pd

    df = pd.read_csv(csv_path)
    C = df.columns

    c_type = _find_col(C, "Type")
    c_air = _find_col(C, "Air temperature [K]", "air_temperature", "Air temperature")
    c_proc = _find_col(C, "Process temperature [K]", "process_temperature",
                       "Process temperature")
    c_rpm = _find_col(C, "Rotational speed [rpm]", "rotational_speed", "Rotational speed")
    c_torque = _find_col(C, "Torque [Nm]", "torque")
    c_wear = _find_col(C, "Tool wear [min]", "tool_wear", "Tool wear")
    c_fail = _find_col(C, "Machine failure", "machine_failure", "target")
    mode_cols = {m: _find_col(C, m) for m in _AI4I_MODES}

    type_map = {"L": 0.0, "M": 1.0, "H": 2.0}
    X, y = [], []
    for _, r in df.iterrows():
        # label
        active = [m for m in _AI4I_PRIORITY if int(r[mode_cols[m]]) == 1]
        if active:
            label = _AI4I_MODES[active[0]]
        elif int(r[c_fail]) == 0:
            label = "no_failure"
        else:
            label = "unknown_failure"
        if failures_only and label in ("no_failure",):
            continue

        air = float(r[c_air]); proc = float(r[c_proc])
        rpm = float(r[c_rpm]); torque = float(r[c_torque]); wear = float(r[c_wear])
        power = torque * rpm * 2 * math.pi / 60.0     # mechanical power (W)
        feats = [
            type_map.get(str(r[c_type]).strip().upper(), 0.0),
            air, proc, proc - air,
            rpm, torque, power, wear, wear * torque,
        ]
        X.append(feats)
        y.append(label)
    return X, y, list(AI4I_FEATURES)


LOADERS = {
    "ai4i": load_ai4i,
}
