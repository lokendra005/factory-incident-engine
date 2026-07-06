"""Central configuration and physical/plant constants.

Everything tunable lives here so the simulator, ingestion validators, and the
reconstruction heuristics all agree on the same ground truth. If a sensor bound
changes, it changes in exactly one place.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FIE_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"          # simulator output (raw JSONL "off the wire")
RUNS_DIR = DATA_DIR / "runs"        # captured agent run traces (for replay)
GOLDEN_DIR = DATA_DIR / "golden"    # labeled evaluation incidents
DB_PATH = Path(os.environ.get("FIE_DB", DATA_DIR / "plant.db"))

# --- plant model -----------------------------------------------------------
# A small, legible fleet. Depth over breadth: one asset class done properly.
MACHINES = ["CNC-17", "CNC-18", "CNC-19", "PRESS-02"]

# Telemetry signals we ingest, with the physically-plausible bounds used by
# BOTH the simulator (to know when it is injecting an "impossible" value) and
# the ingestion validator (to reject them). Units in comments.
SIGNAL_BOUNDS: dict[str, tuple[float, float]] = {
    "spindle_temp_c": (10.0, 140.0),      # deg C; nominal ~55
    "vibration_mm_s": (0.0, 45.0),        # mm/s RMS; nominal ~2.5
    "spindle_load_pct": (0.0, 100.0),     # %
    "coolant_flow_lpm": (0.0, 60.0),      # litres/min; nominal ~28
    "throughput_pph": (0.0, 400.0),       # parts/hour; nominal ~180
    "defect_rate_pct": (0.0, 100.0),      # % of parts
}

# Nominal (healthy) operating point per signal — the baseline the simulator
# oscillates around and the heuristics treat as "normal".
NOMINAL: dict[str, float] = {
    "spindle_temp_c": 55.0,
    "vibration_mm_s": 2.5,
    "spindle_load_pct": 62.0,
    "coolant_flow_lpm": 28.0,
    "throughput_pph": 180.0,
    "defect_rate_pct": 1.2,
}

# Sampling: one telemetry frame per machine every SAMPLE_SECONDS.
SAMPLE_SECONDS = 60

# Plausible timestamp horizon. Frames outside this window are treated as clock
# skew / corruption and dead-lettered. Kept as fixed constants (not wall-clock)
# so ingestion is deterministic and reproducible in CI.
TS_MIN_ISO = "2020-01-01T00:00:00+00:00"
TS_MAX_ISO = "2030-01-01T00:00:00+00:00"

# How often ingestion writes its checkpoint (in committed lines). Smaller =>
# less re-work after a crash; larger => fewer writes. Crash safety does not
# depend on this value because idempotency makes reprocessing harmless.
CHECKPOINT_EVERY = 500

# --- reliability gate thresholds ------------------------------------------
# Below GATE_MIN_SCORE an asset is BLOCKED: the agent must not act on data it
# cannot trust. See fie/reliability.py.
GATE_MIN_SCORE = 0.70
# Fraction of expected frames that may be missing before "gap" penalty bites.
MAX_GAP_RATIO = 0.15
# A source is "stale" if its newest frame is older than this many samples.
STALE_SAMPLES = 5

# --- agent backend ---------------------------------------------------------
# "auto" resolution order: an explicit FIE_ENGINE wins; else Grok if an xAI key
# is present; else Claude if an Anthropic key is present; else the deterministic
# rule-based engine. Every LLM path falls back to rule-based on any error, so
# the demo/eval/CI never require a network or a key.
#   values: auto | rule | rule-1.1 | claude | grok | ml
ENGINE = os.environ.get("FIE_ENGINE", "auto")
CLAUDE_MODEL = os.environ.get("FIE_CLAUDE_MODEL", "claude-opus-4-8")

# Grok / xAI (OpenAI-compatible REST API — no SDK needed, just httpx).
# Get a free key at https://console.x.ai and export it:  export XAI_API_KEY=...
# Model names change; check the console and override with FIE_GROK_MODEL.
GROK_MODEL = os.environ.get("FIE_GROK_MODEL", "grok-2-latest")
GROK_BASE_URL = os.environ.get("FIE_GROK_BASE_URL", "https://api.x.ai/v1")

# Where trained ML models are stored (see fie/ml/).
MODELS_DIR = DATA_DIR / "models"
DATASET_DIR = DATA_DIR / "dataset"


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, RUNS_DIR, GOLDEN_DIR, MODELS_DIR, DATASET_DIR):
        d.mkdir(parents=True, exist_ok=True)
