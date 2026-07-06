"""Synthetic labeled-dataset generation for training the ML engine.

Balances classes by generating N jittered variants per category from the
scenario catalog, extracting the SAME feature vector used at serving time.
Writes JSONL (inspectable) and returns arrays ready for scikit-learn.
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from .. import config
from ..agent.features import FEATURE_NAMES, extract_features, features_vector
from ..reliability import assess
from ..simulator.scenarios import SCENARIOS
from ..simulator.generate import build_variant


def generate_dataset(n_per_class: int = 300, seed: int = 13,
                     out: Path | None = None, write: bool = True):
    """Return (X, y, rows). X: list[list[float]], y: list[str], rows: dicts."""
    by_cat: dict[str, list] = defaultdict(list)
    for sc in SCENARIOS:
        by_cat[sc.category].append(sc)

    rng = random.Random(seed)
    X: list[list[float]] = []
    y: list[str] = []
    rows: list[dict] = []
    for category, scs in sorted(by_cat.items()):
        for i in range(n_per_class):
            sc = scs[i % len(scs)]
            bundle, label = build_variant(sc, rng, i)
            rel = assess(bundle).overall
            feats = extract_features(bundle, rel)
            vec = [feats[name] for name in FEATURE_NAMES]
            X.append(vec)
            y.append(label)
            rows.append({"label": label, "asset": bundle.asset, "features": feats})

    if write:
        out = Path(out or (config.DATASET_DIR / "train.jsonl"))
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return X, y, rows
