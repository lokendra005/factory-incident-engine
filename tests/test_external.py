"""External real-dataset training track (AI4I 2020 loader).

Uses a small fixture in the exact AI4I column format so the loader + training
are verified without needing the actual Kaggle download.
"""
import csv
import random

import pytest

# the ML track needs pandas + scikit-learn; skip cleanly on a core-only install
pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from fie.ml.external import AI4I_FEATURES, load_ai4i        # noqa: E402
from fie.ml.train import train_external                    # noqa: E402

_COLS = ["UDI", "Product ID", "Type", "Air temperature [K]",
         "Process temperature [K]", "Rotational speed [rpm]", "Torque [Nm]",
         "Tool wear [min]", "Machine failure", "TWF", "HDF", "PWF", "OSF", "RNF"]


def _write_ai4i(path):
    rng = random.Random(0)
    rows, uid = [], 1

    def add(n, mode, mut):
        nonlocal uid
        for _ in range(n):
            b = {"air": 300 + rng.gauss(0, 1), "proc": 310 + rng.gauss(0, 1),
                 "rpm": 1500, "torque": 40.0, "wear": rng.randint(0, 180)}
            mut(b)
            m = {k: 0 for k in ("TWF", "HDF", "PWF", "OSF", "RNF")}
            fail = 0
            if mode:
                m[mode] = 1; fail = 1
            rows.append([uid, f"L{uid}", rng.choice("LMH"), round(b["air"], 1),
                         round(b["proc"], 1), int(b["rpm"]), round(b["torque"], 1),
                         int(b["wear"]), fail, m["TWF"], m["HDF"], m["PWF"],
                         m["OSF"], m["RNF"]])
            uid += 1

    add(200, None, lambda b: None)
    add(60, "HDF", lambda b: b.update(proc=b["air"] + 5, rpm=1300))
    add(60, "PWF", lambda b: b.update(rpm=2800, torque=70.0))
    add(60, "OSF", lambda b: b.update(wear=220, torque=60.0))
    add(60, "TWF", lambda b: b.update(wear=rng.randint(200, 240)))
    rng.shuffle(rows)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(_COLS); w.writerows(rows)


def test_ai4i_loader_shapes(tmp_path):
    csv_path = tmp_path / "ai4i.csv"
    _write_ai4i(csv_path)
    X, y, names = load_ai4i(csv_path)
    assert names == AI4I_FEATURES
    assert len(X) == len(y) > 0
    assert len(X[0]) == len(AI4I_FEATURES)
    assert "no_failure" in y and "heat_dissipation" in y


def test_ai4i_training_produces_ext_model(tmp_path):
    csv_path = tmp_path / "ai4i.csv"
    _write_ai4i(csv_path)
    res = train_external("ai4i", str(csv_path), seed=1,
                         out=tmp_path / "ext-ai4i-test.joblib")
    assert res["val_accuracy"] > 0.8
    assert "heat_dissipation" in res["classes"]
    assert "report" in res


def test_failures_only_drops_no_failure(tmp_path):
    csv_path = tmp_path / "ai4i.csv"
    _write_ai4i(csv_path)
    _, y, _ = load_ai4i(csv_path, failures_only=True)
    assert "no_failure" not in y
