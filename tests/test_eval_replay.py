from fie.eval import evaluate
from fie.eval.golden import load_golden
from fie.replay import run_regression
from fie.replay.replay import capture_baseline, replay_trace


def test_fixed_engine_scores_perfectly():
    rep = evaluate("rule-based/1.2.0")
    assert rep.accuracy == 1.0
    assert rep.pass_rate == 1.0
    assert rep.groundedness_mean >= 0.99


def test_buggy_engine_is_worse():
    good = evaluate("rule-based/1.2.0")
    bad = evaluate("rule-based/1.1.0")
    assert bad.accuracy < good.accuracy
    assert bad.failing()          # it has real failures


def test_regression_says_ship_with_no_regressions():
    rep = run_regression("rule-based/1.1.0", "rule-based/1.2.0")
    assert rep.fixed > 0
    assert rep.regressed == 0
    assert rep.verdict == "SHIP"


def test_reverse_regression_is_held():
    rep = run_regression("rule-based/1.2.0", "rule-based/1.1.0")
    assert rep.regressed > 0
    assert rep.verdict == "HOLD"


def test_replay_uses_snapshot_and_is_deterministic():
    cases = load_golden()
    traces = capture_baseline("rule-based/1.1.0", cases=cases, save=False)
    tr = traces[0]
    a = replay_trace(tr, "rule-based/1.2.0").report
    b = replay_trace(tr, "rule-based/1.2.0").report
    assert a.root_cause_category == b.root_cause_category
    # replaying the SAME engine reproduces the original verdict exactly
    same = replay_trace(tr, "rule-based/1.1.0").report
    assert same.root_cause_category == tr.report.root_cause_category
