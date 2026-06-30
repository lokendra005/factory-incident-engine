"""Scenario catalog — the ground truth of the simulated plant.

Each scenario describes how signals deviate from nominal over an incident
window, what maintenance/MES context surrounds it, and the *labels* the
evaluation harness scores against. The catalog is intentionally small but
covers physically distinct failure modes that are easy to confuse — which is
what makes the evaluation meaningful (see docs/failure-model.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# How one signal behaves across the incident window.
#   flat     : stays at nominal (+noise)
#   linear   : ramps nominal -> target across the window
#   step_at  : nominal until `at_min`, then holds `target` (sensor step/flatline)
#   spike_at : nominal, brief excursion to `target` around `at_min`
Mode = Literal["flat", "linear", "step_at", "spike_at"]


@dataclass(frozen=True)
class Effect:
    signal: str
    mode: Mode
    target: float = 0.0
    at_min: int = 0


@dataclass(frozen=True)
class MaintenanceSpec:
    # offset_min is relative to incident start (negative => before the incident)
    offset_min: int
    kind: str
    component: str
    note: str
    closed: bool = True
    technician: str = ""


@dataclass(frozen=True)
class MesSpec:
    at_min: int
    event: str
    detail: str = ""
    code: str = ""


@dataclass(frozen=True)
class Scenario:
    key: str
    asset: str
    category: str
    title: str
    duration_min: int
    effects: list[Effect]
    maintenance: list[MaintenanceSpec] = field(default_factory=list)
    mes: list[MesSpec] = field(default_factory=list)
    # ---- evaluation labels ----
    expected_category: str = ""
    expected_root_cause_kw: list[str] = field(default_factory=list)
    # signals that MUST be cited for a grounded answer
    key_signals: list[str] = field(default_factory=list)
    expects_missing_evidence: bool = False
    # a data gap (minutes with no telemetry) to force the "insufficient data" path
    gap_min: tuple[int, int] | None = None
    notes: str = ""

    def __post_init__(self):
        object.__setattr__(self, "expected_category",
                           self.expected_category or self.category)


# --------------------------------------------------------------------------
# The catalog. `catalog()` parameterizes these across assets to yield the
# full golden set without hand-writing every incident.
# --------------------------------------------------------------------------

def _cooling(asset: str, key: str) -> Scenario:
    return Scenario(
        key=key, asset=asset, category="cooling_degradation",
        title="Coolant flow collapse -> spindle overheat",
        duration_min=40,
        effects=[
            Effect("coolant_flow_lpm", "linear", target=6.0),
            Effect("spindle_temp_c", "linear", target=122.0),
            Effect("spindle_load_pct", "flat"),
            Effect("vibration_mm_s", "flat"),
        ],
        maintenance=[
            MaintenanceSpec(-14400, "inspection", "coolant_pump",
                            "Coolant pump flow marginal; recommend reseal at next PM.",
                            closed=True, technician="R. Okafor"),
        ],
        mes=[
            MesSpec(31, "error_code", "Spindle thermal warning", "E-THERM-01"),
            MesSpec(37, "shutdown", "Controller thermal trip", "E-THERM-02"),
        ],
        expected_root_cause_kw=["coolant", "cooling", "flow", "overheat"],
        key_signals=["coolant_flow_lpm", "spindle_temp_c"],
        notes="Real thermal event: temp rise is CORRELATED with coolant drop.",
    )


def _sensor_fault(asset: str, key: str) -> Scenario:
    # The trap: temp appears high, but coolant flow + load are nominal. A naive
    # engine calls this cooling_degradation; the correct answer is sensor_fault.
    return Scenario(
        key=key, asset=asset, category="sensor_fault",
        title="Spindle temp sensor step fault (false overheat)",
        duration_min=40,
        effects=[
            Effect("spindle_temp_c", "step_at", target=119.0, at_min=12),
            Effect("coolant_flow_lpm", "flat"),   # <- still nominal: the tell
            Effect("spindle_load_pct", "flat"),
            Effect("vibration_mm_s", "flat"),
        ],
        maintenance=[
            MaintenanceSpec(-20160, "calibration", "temp_sensor_T19",
                            "Temp sensor T-19 flagged noisy; recalibration deferred.",
                            closed=False, technician="L. Persson"),
        ],
        mes=[MesSpec(13, "error_code", "Spindle thermal warning", "E-THERM-01")],
        expected_root_cause_kw=["sensor", "temp", "false", "spurious", "instrument"],
        key_signals=["spindle_temp_c", "coolant_flow_lpm"],
        notes="Step change with NO coolant drop and NO load change => instrument fault.",
    )


def _bearing(asset: str, key: str) -> Scenario:
    return Scenario(
        key=key, asset=asset, category="bearing_wear",
        title="Spindle bearing wear -> vibration -> fault",
        duration_min=50,
        effects=[
            Effect("vibration_mm_s", "linear", target=18.0),
            Effect("throughput_pph", "linear", target=120.0),
            Effect("spindle_temp_c", "linear", target=78.0),
            Effect("coolant_flow_lpm", "flat"),
        ],
        maintenance=[
            MaintenanceSpec(-100800, "replace", "spindle_bearing",
                            "Bearing set replaced (18 months prior).", closed=True,
                            technician="R. Okafor"),
        ],
        mes=[MesSpec(46, "error_code", "Vibration threshold exceeded", "E-VIB-04")],
        expected_root_cause_kw=["vibration", "bearing", "mechanical"],
        key_signals=["vibration_mm_s"],
    )


def _tool_wear(asset: str, key: str) -> Scenario:
    return Scenario(
        key=key, asset=asset, category="tool_wear",
        title="Gradual tool wear -> rising defect rate",
        duration_min=90,
        effects=[
            Effect("defect_rate_pct", "linear", target=11.0),
            Effect("throughput_pph", "linear", target=150.0),
            Effect("spindle_temp_c", "flat"),
            Effect("vibration_mm_s", "flat"),
        ],
        mes=[MesSpec(80, "error_code", "Quality alarm: defect rate high", "E-QAL-07")],
        expected_root_cause_kw=["tool", "wear", "defect", "quality"],
        key_signals=["defect_rate_pct"],
    )


def _overload(asset: str, key: str) -> Scenario:
    return Scenario(
        key=key, asset=asset, category="overload",
        title="Sustained spindle overload -> thermal rise",
        duration_min=45,
        effects=[
            Effect("spindle_load_pct", "linear", target=99.0),
            Effect("spindle_temp_c", "linear", target=110.0),
            Effect("coolant_flow_lpm", "flat"),   # coolant fine; load is the driver
            Effect("vibration_mm_s", "linear", target=6.0),
        ],
        mes=[
            MesSpec(2, "config_change", "Feed rate override +25% by operator", "CFG-FEED"),
            MesSpec(40, "error_code", "Spindle thermal warning", "E-THERM-01"),
        ],
        expected_root_cause_kw=["load", "overload", "feed", "sustained"],
        key_signals=["spindle_load_pct", "spindle_temp_c"],
        notes="Temp rises but coolant is nominal and LOAD is pinned => overload.",
    )


def _operator_config(asset: str, key: str) -> Scenario:
    return Scenario(
        key=key, asset=asset, category="operator_config",
        title="Operator config change -> defect + vibration rise",
        duration_min=60,
        effects=[
            Effect("defect_rate_pct", "step_at", target=7.5, at_min=10),
            Effect("vibration_mm_s", "step_at", target=9.0, at_min=10),
            Effect("spindle_temp_c", "flat"),
        ],
        mes=[MesSpec(9, "config_change", "Spindle RPM profile changed by operator", "CFG-RPM")],
        expected_root_cause_kw=["config", "operator", "rpm", "change"],
        key_signals=["defect_rate_pct", "vibration_mm_s"],
        notes="Degradation begins immediately AFTER a config_change event.",
    )


def _no_incident(asset: str, key: str) -> Scenario:
    return Scenario(
        key=key, asset=asset, category="no_incident",
        title="Nominal operation (control case)",
        duration_min=40,
        effects=[Effect("spindle_temp_c", "flat")],
        expected_root_cause_kw=["no", "nominal", "normal"],
        key_signals=[],
        notes="No anomaly. A good engine must NOT invent a root cause.",
    )


def _insufficient(asset: str, key: str) -> Scenario:
    # Telemetry drops out for most of the window -> engine must decline.
    return Scenario(
        key=key, asset=asset, category="unknown",
        title="Telemetry outage during suspected event",
        duration_min=40,
        effects=[Effect("spindle_temp_c", "linear", target=95.0)],
        mes=[MesSpec(38, "shutdown", "Unexpected stop", "E-STOP")],
        expected_root_cause_kw=["insufficient", "unknown", "missing", "data"],
        key_signals=[],
        expects_missing_evidence=True,
        gap_min=(5, 36),
        notes="72% of the window has no telemetry => confidence must be low.",
    )


_BUILDERS = [
    _cooling, _sensor_fault, _bearing, _tool_wear,
    _overload, _operator_config, _no_incident, _insufficient,
]


def catalog() -> list[Scenario]:
    """The full golden set: each failure mode instantiated on rotating assets."""
    assets = ["CNC-17", "CNC-18", "CNC-19", "PRESS-02"]
    out: list[Scenario] = []
    for i, build in enumerate(_BUILDERS):
        # instantiate each builder twice on different assets -> 16 incidents
        for j in range(2):
            asset = assets[(i + j) % len(assets)]
            key = f"{build.__name__.strip('_')}-{asset}-{j}"
            out.append(build(asset, key))
    return out


SCENARIOS = catalog()
