"""Loader for the Microsoft Azure Predictive Maintenance dataset.

This is the richer real-dataset track: it consumes the five source files
(telemetry, errors, maintenance, failures, machines) and does the data
engineering the project is really about — a multi-source join, windowing around
each failure event, and temporal features (signal statistics over a lookback
window, error counts, hours-since-last-maintenance per component, machine
age/model). The label is the component that failed.

Like the AI4I track this is a *separate* real-dataset model (its own feature and
label space), saved under `ext-*` and not served by the CNC reconstruction
engine. What it demonstrates is the end-to-end shape on real, multi-file,
temporal plant data.

Expected files in `data_dir` (standard Azure PdM names):
    PdM_telemetry.csv  datetime, machineID, volt, rotate, pressure, vibration
    PdM_errors.csv     datetime, machineID, errorID
    PdM_maint.csv      datetime, machineID, comp
    PdM_failures.csv   datetime, machineID, failure
    PdM_machines.csv   machineID, model, age
"""
from __future__ import annotations

import random
from pathlib import Path

SIGNALS = ["volt", "rotate", "pressure", "vibration"]
_STATS = ["mean", "std", "min", "max"]
# Canonical Azure PdM error/component sets. Pinning these keeps the feature
# vector stable (27 features) regardless of which types happen to appear in a
# given slice of the data — avoiding a data-dependent feature contract.
_CANON_ERRORS = [f"error{i}" for i in range(1, 6)]
_CANON_COMPS = [f"comp{i}" for i in range(1, 5)]


def _read(dd: Path, name: str, parse_dt: bool):
    import pandas as pd
    df = pd.read_csv(dd / name)
    df.columns = [c.strip().lower() for c in df.columns]
    if parse_dt and "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def load_azure_pdm(data_dir, window_hours: int = 24, horizon_hours: int = 24,
                   neg_ratio: int = 2, seed: int = 13, **_ignore):
    import numpy as np
    import pandas as pd

    dd = Path(data_dir)
    tel = _read(dd, "PdM_telemetry.csv", True)
    err = _read(dd, "PdM_errors.csv", True)
    mnt = _read(dd, "PdM_maint.csv", True)
    fail = _read(dd, "PdM_failures.csv", True)
    mach = _read(dd, "PdM_machines.csv", False)

    # use the canonical sets when the data matches them (the real dataset does),
    # otherwise fall back to whatever the data contains
    err_types = (_CANON_ERRORS if set(err["errorid"].unique()) <= set(_CANON_ERRORS)
                 else sorted(err["errorid"].unique()))
    comp_types = (_CANON_COMPS if set(mnt["comp"].unique()) <= set(_CANON_COMPS)
                  else sorted(mnt["comp"].unique()))
    models = sorted(mach["model"].unique())
    model_code = {m: i for i, m in enumerate(models)}
    mach = mach.set_index("machineid")

    # per-machine telemetry indexed by time for fast window slicing
    tel = tel.sort_values(["machineid", "datetime"])
    tel_by_m = {mid: g.set_index("datetime")[SIGNALS]
                for mid, g in tel.groupby("machineid")}
    err_by_m = {mid: g for mid, g in err.groupby("machineid")}
    # maintenance per (machine, comp), sorted times
    mnt_by_mc = {(mid, c): g["datetime"].sort_values().values
                 for (mid, c), g in mnt.groupby(["machineid", "comp"])}

    win = pd.Timedelta(hours=window_hours)
    horizon = pd.Timedelta(hours=horizon_hours)

    feature_names = (
        [f"{s}_{st}" for s in SIGNALS for st in _STATS]
        + [f"err_{e}_count" for e in err_types]
        + [f"hours_since_{c}" for c in comp_types]
        + ["machine_age", "model_code"]
    )

    def features(mid, t_end):
        g = tel_by_m.get(mid)
        if g is None:
            return None
        w = g.loc[t_end - win: t_end]
        if len(w) < 3:
            return None
        feats = []
        for s in SIGNALS:
            col = w[s].to_numpy()
            feats += [float(col.mean()), float(col.std()),
                      float(col.min()), float(col.max())]
        eg = err_by_m.get(mid)
        for e in err_types:
            if eg is None:
                feats.append(0.0)
            else:
                m = (eg["datetime"] >= t_end - win) & (eg["datetime"] <= t_end) & (eg["errorid"] == e)
                feats.append(float(m.sum()))
        for c in comp_types:
            times = mnt_by_mc.get((mid, c))
            if times is None or len(times) == 0:
                feats.append(1e4)
            else:
                prior = times[times <= np.datetime64(t_end)]
                if len(prior) == 0:
                    feats.append(1e4)
                else:
                    dt = (np.datetime64(t_end) - prior[-1]) / np.timedelta64(1, "h")
                    feats.append(float(dt))
        age = float(mach.loc[mid, "age"]) if mid in mach.index else 0.0
        mdl = model_code.get(mach.loc[mid, "model"], 0) if mid in mach.index else 0
        feats += [age, float(mdl)]
        return feats

    X, y = [], []

    # positive windows: one per failure event, labelled by failed component
    fail_times: dict[int, list] = {}
    for _, r in fail.iterrows():
        mid, t = int(r["machineid"]), r["datetime"]
        fail_times.setdefault(mid, []).append(t)
        f = features(mid, t)
        if f is not None:
            X.append(f)
            y.append(str(r["failure"]))

    n_pos = len(y)

    # negative windows: random times far from any failure for that machine
    rng = random.Random(seed)
    machine_ids = list(tel_by_m.keys())
    target_neg = min(n_pos * neg_ratio, 20000)
    tries = 0
    while sum(1 for v in y if v == "none") < target_neg and tries < target_neg * 20:
        tries += 1
        mid = rng.choice(machine_ids)
        idx = tel_by_m[mid].index
        t = idx[rng.randrange(len(idx))]
        # keep away from failures on this machine
        if any(abs((t - ft) / pd.Timedelta(hours=1)) <= horizon_hours
               for ft in fail_times.get(mid, [])):
            continue
        f = features(mid, t)
        if f is not None:
            X.append(f)
            y.append("none")

    return X, y, feature_names
