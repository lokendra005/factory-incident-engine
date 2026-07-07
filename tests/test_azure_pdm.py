"""Azure PdM multi-source loader — verified on a small 5-file fixture."""
import csv
import random
from datetime import datetime, timedelta

import pytest

pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from fie.ml.azure_pdm import SIGNALS, load_azure_pdm      # noqa: E402
from fie.ml.train import train_external                   # noqa: E402


def _write_fixture(d):
    d.mkdir(parents=True, exist_ok=True)
    rng = random.Random(3)
    start = datetime(2015, 1, 1)
    machines = [1, 2]
    hours = 24 * 12

    with open(d / "PdM_telemetry.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["datetime", "machineID", *SIGNALS])
        for m in machines:
            for h in range(hours):
                t = start + timedelta(hours=h)
                w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"), m,
                            round(170 + rng.gauss(0, 15), 2),
                            round(450 + rng.gauss(0, 50), 2),
                            round(100 + rng.gauss(0, 10), 2),
                            round(40 + rng.gauss(0, 5), 2)])
    with open(d / "PdM_errors.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["datetime", "machineID", "errorID"])
        for m in machines:
            for _ in range(6):
                t = start + timedelta(hours=rng.randint(0, hours - 1))
                w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"), m, f"error{rng.randint(1, 5)}"])
    with open(d / "PdM_maint.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["datetime", "machineID", "comp"])
        for m in machines:
            for c in range(1, 5):
                t = start + timedelta(hours=rng.randint(0, 96))
                w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"), m, f"comp{c}"])
    with open(d / "PdM_failures.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["datetime", "machineID", "failure"])
        for m in machines:
            for _ in range(6):
                t = start + timedelta(hours=rng.randint(48, hours - 1))
                w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"), m, f"comp{rng.randint(1, 4)}"])
    with open(d / "PdM_machines.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["machineID", "model", "age"])
        for m in machines:
            w.writerow([m, f"model{rng.randint(1, 4)}", rng.randint(1, 20)])


def test_azure_loader_builds_windows(tmp_path):
    d = tmp_path / "az"
    _write_fixture(d)
    X, y, names = load_azure_pdm(d, window_hours=24, neg_ratio=2, seed=1)
    assert len(X) == len(y) > 0
    assert len(names) == len(X[0]) == 27            # 16 signal + 5 err + 4 comp + 2
    assert "none" in y                              # negatives sampled
    assert any(v.startswith("comp") for v in y)     # failure windows labelled


def test_azure_training_runs(tmp_path):
    d = tmp_path / "az"
    _write_fixture(d)
    res = train_external("azure_pdm", str(d), seed=1,
                         out=tmp_path / "ext-azure-test.joblib")
    assert res["n_features"] == 27
    assert res["n_samples"] > 0
    assert "report" in res
