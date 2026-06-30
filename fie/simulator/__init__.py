"""Deterministic factory simulator.

Generates realistic telemetry / MES / maintenance data with *known* injected
root causes, and (crucially) injects the kinds of data mess a real plant feed
carries: duplicates, out-of-order frames, impossible values, gaps, a mid-stream
schema change, and malformed lines. The ingestion layer's job is to survive it.
"""
from .scenarios import SCENARIOS, Scenario, catalog          # noqa: F401
from .generate import build_bundle, write_raw_feed            # noqa: F401
