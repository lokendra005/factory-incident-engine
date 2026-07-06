"""Train + persist the ML classifier used by the ML engine.

A small, honest pipeline: StandardScaler + RandomForest, trained on the
synthetic dataset and held-out-scored. The artifact stores the pipeline, the
feature-name contract, and the class list, so serving can detect any skew.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .dataset import generate_dataset

MODEL_VERSION = "rf-1.0.0"


def train_model(n_per_class: int = 300, seed: int = 13,
                out: Path | None = None) -> dict:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    import joblib

    from ..agent.features import FEATURE_NAMES

    X, y, _rows = generate_dataset(n_per_class=n_per_class, seed=seed, write=True)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=seed,
                                          stratify=y)
    clf = Pipeline([
        ("scaler", StandardScaler()),
        ("rf", RandomForestClassifier(n_estimators=200, random_state=seed,
                                      class_weight="balanced")),
    ])
    clf.fit(Xtr, ytr)
    val_acc = float(clf.score(Xte, yte))

    config.ensure_dirs()
    out = Path(out or (config.MODELS_DIR / f"ml-{MODEL_VERSION}.joblib"))
    joblib.dump({
        "version": MODEL_VERSION,
        "pipeline": clf,
        "feature_names": list(FEATURE_NAMES),
        "classes": list(clf.named_steps["rf"].classes_),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_train": len(Xtr),
    }, out)
    return {"version": MODEL_VERSION, "n_train": len(Xtr), "n_val": len(Xte),
            "val_accuracy": round(val_acc, 3), "path": str(out)}


def load_latest(models_dir: Path | None = None) -> dict:
    import joblib
    models_dir = Path(models_dir or config.MODELS_DIR)
    files = sorted(models_dir.glob("ml-*.joblib"))
    if not files:
        raise FileNotFoundError("no trained model; run `fie train` first")
    return joblib.load(files[-1])
