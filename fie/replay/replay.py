"""Trace capture + deterministic replay."""
from __future__ import annotations

from ..agent.engine import get_engine
from ..agent.reconstruct import reconstruct, save_trace
from ..eval.golden import load_golden
from ..models import RunTrace


def capture_baseline(engine_name: str, cases=None, save: bool = True) -> list[RunTrace]:
    """Run an engine over the golden bundles and capture the traces.

    These stand in for "production traces": each one carries the exact inputs
    the engine saw, so it can later be replayed against a candidate engine.
    """
    engine = get_engine(engine_name)
    cases = cases if cases is not None else load_golden()
    traces = []
    for bundle, _labels in cases:
        tr = reconstruct(bundle, engine=engine, save=False)
        if save:
            save_trace(tr)
        traces.append(tr)
    return traces


def replay_trace(trace: RunTrace, new_engine_name: str) -> RunTrace:
    """Replay a captured trace's snapshotted inputs against a new engine.

    Deterministic: uses ``trace.inputs`` (never the live store), so the result
    depends only on the candidate engine, not on data that changed since.
    """
    engine = get_engine(new_engine_name)
    return reconstruct(trace.inputs, engine=engine, save=False)
