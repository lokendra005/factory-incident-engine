"""Train + persist classifiers.

Two tracks share one fit/save core:
  * synthetic  -> ml-rf-*.joblib  (served by the reconstruction MLEngine)
  * external   -> ext-<source>-*.joblib  (a separate real-dataset track; NOT
                  served by MLEngine — see fie/ml/external.py for why)

The artifact always stores the feature-name contract and class list so serving
can detect train/serve skew.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .dataset import generate_dataset

MODEL_VERSION = "rf-1.0.0"


def _fit_and_save(X, y, feature_names, out_path: Path, version: str,
                  seed: int = 13) -> dict:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import classification_report
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    import joblib

    # stratify only when every class has >= 2 samples
    from collections import Counter
    counts = Counter(y)
    strat = y if min(counts.values()) >= 2 and len(counts) > 1 else None
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=seed,
                                          stratify=strat)
    clf = Pipeline([
        ("scaler", StandardScaler()),
        ("rf", RandomForestClassifier(n_estimators=200, random_state=seed,
                                      class_weight="balanced")),
    ])
    clf.fit(Xtr, ytr)
    val_acc = float(clf.score(Xte, yte))
    report = classification_report(yte, clf.predict(Xte), zero_division=0)

    classes = [str(c) for c in clf.named_steps["rf"].classes_]
    config.ensure_dirs()
    out_path = Path(out_path)
    joblib.dump({
        "version": version, "pipeline": clf,
        "feature_names": list(feature_names), "classes": classes,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_train": len(Xtr),
    }, out_path)
    return {"version": version, "n_train": len(Xtr), "n_val": len(Xte),
            "val_accuracy": round(val_acc, 3), "path": str(out_path),
            "classes": classes, "report": report}


def train_model(n_per_class: int = 300, seed: int = 13,
                out: Path | None = None) -> dict:
    """Synthetic track — the model the reconstruction MLEngine serves."""
    from ..agent.features import FEATURE_NAMES
    X, y, _rows = generate_dataset(n_per_class=n_per_class, seed=seed, write=True)
    out = Path(out or (config.MODELS_DIR / f"ml-{MODEL_VERSION}.joblib"))
    return _fit_and_save(X, y, FEATURE_NAMES, out, MODEL_VERSION, seed)


def train_external(source: str, csv_path: str, seed: int = 13,
                   failures_only: bool = False, out: Path | None = None) -> dict:
    """Real-dataset track — e.g. AI4I 2020. Saved under ext-<source>-*.joblib."""
    from .external import LOADERS
    if source not in LOADERS:
        raise ValueError(f"unknown dataset source '{source}'; "
                         f"available: {list(LOADERS)}")
    X, y, feature_names = LOADERS[source](csv_path, failures_only=failures_only)
    version = f"{source}-1.0.0"
    out = Path(out or (config.MODELS_DIR / f"ext-{version}.joblib"))
    res = _fit_and_save(X, y, feature_names, out, version, seed)
    res["source"] = source
    res["n_samples"] = len(X)
    return res


def load_latest(models_dir: Path | None = None) -> dict:
    """Load the latest SYNTHETIC model (ml-*). External ext-* models are
    intentionally excluded so they never serve the reconstruction engine."""
    import joblib
    models_dir = Path(models_dir or config.MODELS_DIR)
    files = sorted(models_dir.glob("ml-*.joblib"))
    if not files:
        raise FileNotFoundError("no trained model; run `fie train` first")
    return joblib.load(files[-1])
