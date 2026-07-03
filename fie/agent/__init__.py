"""The incident-reconstruction agent.

A reconstruction is a PURE function of an EvidenceBundle: the engine queries the
bundle through a small toolbox (query_telemetry / search_maintenance /
find_similar_incidents), reasons about the signal signature, and emits a grounded
IncidentReport. Purity is what makes replay deterministic.

Two engine *versions* ship intentionally: rule-based/1.1.0 has a real bug
(any temperature rise is blamed on cooling) and rule-based/1.2.0 fixes it. The
replay harness uses this to demonstrate regression detection on captured runs.
"""
from .engine import RuleBasedEngine, get_engine, ENGINES        # noqa: F401
from .tools import Toolbox                                       # noqa: F401
from .reconstruct import reconstruct, reconstruct_from_store     # noqa: F401
