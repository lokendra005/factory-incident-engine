from fie.reliability import assess
from fie.simulator import SCENARIOS
from fie.simulator.generate import build_bundle


def _scenario(cat):
    return next(s for s in SCENARIOS if s.category == cat)


def test_full_coverage_not_blocked():
    bundle, _ = build_bundle(_scenario("cooling_degradation"))
    rep = assess(bundle)
    assert rep.overall > 0.9
    assert not rep.blocked


def test_telemetry_gap_blocks_deployment():
    bundle, labels = build_bundle(_scenario("unknown"))   # has a big gap
    rep = assess(bundle)
    assert rep.blocked
    assert rep.overall < 0.7
    assert "below" in rep.reason.lower()


def test_scores_are_deterministic():
    b1, _ = build_bundle(_scenario("bearing_wear"))
    b2, _ = build_bundle(_scenario("bearing_wear"))
    assert assess(b1).model_dump() == assess(b2).model_dump()
