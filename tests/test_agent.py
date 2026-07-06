import pytest

from fie.agent import get_engine
from fie.agent.reconstruct import reconstruct
from fie.simulator import SCENARIOS
from fie.simulator.generate import build_bundle

V12 = get_engine("rule-based/1.2.0")
V11 = get_engine("rule-based/1.1.0")


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.key for s in SCENARIOS])
def test_v12_classifies_every_scenario(sc):
    bundle, labels = build_bundle(sc)
    tr = reconstruct(bundle, engine=V12, save=False)
    assert tr.report.root_cause_category == labels["expected_category"], sc.notes


def test_v11_bug_calls_sensor_fault_a_cooling_fault():
    sc = next(s for s in SCENARIOS if s.category == "sensor_fault")
    bundle, _ = build_bundle(sc)
    got = reconstruct(bundle, engine=V11, save=False).report.root_cause_category
    assert got == "cooling_degradation"      # the documented v1.1 bug


def test_every_cited_id_resolves(store=None):
    """Grounding: the agent must never cite evidence that isn't in the bundle."""
    for sc in SCENARIOS:
        bundle, _ = build_bundle(sc)
        tr = reconstruct(bundle, engine=V12, save=False)
        valid = ({r.id for r in bundle.readings}
                 | {m.id for m in bundle.maintenance}
                 | {e.id for e in bundle.mes})
        assert tr.report.cited_ids() <= valid, sc.key


def test_insufficient_data_is_gated_not_guessed():
    sc = next(s for s in SCENARIOS if s.category == "unknown")
    bundle, _ = build_bundle(sc)
    tr = reconstruct(bundle, engine=V12, save=False)
    assert tr.report.blocked
    assert tr.report.missing_evidence
    assert tr.report.confidence < 0.3


def test_reconstruction_is_deterministic():
    sc = SCENARIOS[0]
    b, _ = build_bundle(sc)
    a = reconstruct(b, engine=V12, save=False).report
    c = reconstruct(b, engine=V12, save=False).report
    assert a.root_cause_category == c.root_cause_category
    assert a.confidence == c.confidence
    assert [t.ts for t in a.timeline] == [t.ts for t in c.timeline]
