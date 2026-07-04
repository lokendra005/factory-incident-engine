"""Golden dataset: labeled incidents the harness scores against.

Deterministically generated from the scenario catalog and persisted as JSON so
the dataset is inspectable and reviewable ("show me the evaluation dataset").
Each file holds the exact EvidenceBundle the engine will see plus its labels.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import config
from ..models import EvidenceBundle
from ..simulator import SCENARIOS
from ..simulator.generate import build_bundle


def build_golden(out_dir: Path | None = None) -> int:
    out_dir = Path(out_dir or config.GOLDEN_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        old.unlink()
    for sc in SCENARIOS:
        bundle, labels = build_bundle(sc)
        payload = {"labels": labels, "bundle": bundle.model_dump()}
        (out_dir / f"{sc.key}.json").write_text(json.dumps(payload, indent=2))
    return len(SCENARIOS)


def load_golden(golden_dir: Path | None = None) -> list[tuple[EvidenceBundle, dict]]:
    """Load persisted golden cases; if none on disk, build them in-memory."""
    golden_dir = Path(golden_dir or config.GOLDEN_DIR)
    files = sorted(golden_dir.glob("*.json"))
    if not files:
        return [build_bundle(sc) for sc in SCENARIOS]
    out = []
    for f in files:
        payload = json.loads(f.read_text())
        out.append((EvidenceBundle.model_validate(payload["bundle"]), payload["labels"]))
    return out
