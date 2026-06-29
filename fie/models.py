"""Canonical domain models.

Raw plant data is messy and untyped (that is the whole point of the ingestion
layer). Once a record survives validation it becomes one of the strict models
below and nothing downstream ever sees the mess again.

Design note: the reconstruction engine is a PURE function of an ``EvidenceBundle``
-> (``IncidentReport``, tool calls). That purity is what makes replay
deterministic: we snapshot the bundle into the run trace, so a new engine
version can be run against the exact same inputs the old version saw.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Canonical plant records
# --------------------------------------------------------------------------

Source = Literal["telemetry", "maintenance", "mes"]


class TelemetryReading(BaseModel):
    id: str                       # deterministic hash -> idempotency key
    machine: str
    ts: str                       # ISO-8601 UTC, e.g. 2026-07-01T14:03:00+00:00
    signal: str
    value: float
    source: str = "telemetry"


class MaintenanceRecord(BaseModel):
    id: str
    machine: str
    ts: str
    kind: Literal["inspection", "repair", "replace", "lubrication", "calibration"]
    component: str
    note: str = ""
    closed: bool = True
    technician: str = ""
    source: str = "maintenance"


class MesEvent(BaseModel):
    id: str
    machine: str
    ts: str
    # Manufacturing Execution System events: state + config changes, faults.
    event: Literal[
        "startup", "shutdown", "config_change", "error_code",
        "state_change", "order_start", "order_complete",
    ]
    detail: str = ""
    code: str = ""
    source: str = "mes"


# --------------------------------------------------------------------------
# Evidence bundle -> the pure input to the reconstruction engine
# --------------------------------------------------------------------------

class PriorIncident(BaseModel):
    """Minimal record of a past incident, used by find_similar_incidents."""
    incident_id: str
    asset: str
    root_cause_category: str
    window_start: str
    window_end: str
    summary: str = ""


class EvidenceBundle(BaseModel):
    asset: str
    window_start: str
    window_end: str
    readings: list[TelemetryReading] = Field(default_factory=list)
    maintenance: list[MaintenanceRecord] = Field(default_factory=list)
    mes: list[MesEvent] = Field(default_factory=list)
    past_incidents: list[PriorIncident] = Field(default_factory=list)
    # Per-source reliability at reconstruction time (0..1). The gate uses this.
    reliability: dict[str, float] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Incident report -> the engine's output
# --------------------------------------------------------------------------

RootCauseCategory = Literal[
    "cooling_degradation",     # coolant flow drop -> temp rise
    "bearing_wear",            # rising vibration -> fault
    "sensor_fault",            # stuck / drifting sensor, not a real process fault
    "tool_wear",               # gradual defect-rate rise
    "overload",                # sustained spindle load -> thermal trip
    "operator_config",         # a config change preceded degradation
    "unknown",                 # insufficient / conflicting evidence
    "no_incident",             # nothing abnormal in the window
]


class TimelineEntry(BaseModel):
    ts: str
    description: str
    signal: Optional[str] = None
    severity: Literal["info", "warn", "critical"] = "info"
    evidence_ids: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """A citation. Every claim in the report must resolve to one of these."""
    id: str                       # canonical record id being cited
    kind: Source
    summary: str


class IncidentReport(BaseModel):
    incident_id: str
    asset: str
    window_start: str
    window_end: str

    root_cause: str = "Undetermined"
    root_cause_category: RootCauseCategory = "unknown"
    confidence: float = 0.0        # 0..1

    timeline: list[TimelineEntry] = Field(default_factory=list)
    supporting_evidence: list[Evidence] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    similar_incidents: list[str] = Field(default_factory=list)

    # provenance / gating
    engine: str = ""
    agent_version: str = ""
    prompt_version: str = ""
    generated_at: str = ""
    data_reliability: float = 1.0
    blocked: bool = False          # True => data-quality gate refused to act
    blocked_reason: str = ""

    def cited_ids(self) -> set[str]:
        ids = {e.id for e in self.supporting_evidence}
        for t in self.timeline:
            ids.update(t.evidence_ids)
        return ids


# --------------------------------------------------------------------------
# Run trace -> captured for deterministic replay
# --------------------------------------------------------------------------

class ToolCall(BaseModel):
    name: str
    args: dict = Field(default_factory=dict)
    result_count: int = 0
    result_ids: list[str] = Field(default_factory=list)
    ok: bool = True
    note: str = ""


class RunTrace(BaseModel):
    run_id: str
    incident_id: str
    asset: str
    window_start: str
    window_end: str
    engine: str
    agent_version: str
    prompt_version: str
    created_at: str
    # The exact inputs the engine saw. Snapshotting these is what makes replay
    # deterministic and independent of later changes to the store.
    inputs: EvidenceBundle
    tool_calls: list[ToolCall] = Field(default_factory=list)
    report: IncidentReport
