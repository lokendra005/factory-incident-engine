"""Console action runner. Offline + fast (no server socket needed)."""
from fie.web.actions import run_action


def test_unknown_action_is_rejected():
    r = run_action("not-a-real-action", {})
    assert r["ok"] is False
    assert "unknown action" in r["log"]


def test_recover_dlq_action_runs():
    r = run_action("recover-dlq", {})
    assert r["ok"] is True
    assert "recover" in r["log"].lower()


def test_eval_action_returns_log():
    r = run_action("eval", {"engine": "rule-based/1.2.0"})
    assert "acc=" in r["log"]
