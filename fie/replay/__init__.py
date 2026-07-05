"""Deterministic replay + regression detection.

Every reconstruction snapshots the exact evidence it saw into its RunTrace.
Replay feeds that snapshot to a *different* engine version, so we can ask the
central FDE question: "if we ship this change, does it fix the bugs we know
about — without breaking anything that currently works?"
"""
from .replay import replay_trace, capture_baseline           # noqa: F401
from .regression import run_regression, RegressionReport      # noqa: F401
