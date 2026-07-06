"""LLM (Grok/Claude) resolution + ML engine training/serving.

Fully offline: the LLM tests only check no-key fallback (never hit the network),
and the ML test trains a small model into the temp data dir from conftest.
"""
import os

from fie.agent import get_engine


def test_grok_falls_back_without_key():
    os.environ.pop("XAI_API_KEY", None)
    os.environ.pop("GROK_API_KEY", None)
    assert get_engine("grok").name.startswith("rule-based")


def test_claude_falls_back_without_key():
    os.environ.pop("ANTHROPIC_API_KEY", None)
    assert get_engine("claude").name.startswith("rule-based")


def test_ml_engine_trains_scores_and_serves():
    from fie.ml.train import train_model
    from fie.eval import evaluate
    from fie.agent.reconstruct import reconstruct
    from fie.simulator import SCENARIOS
    from fie.simulator.generate import build_bundle

    res = train_model(n_per_class=40, seed=1)     # small + fast
    assert res["val_accuracy"] > 0.8

    eng = get_engine("ml")
    assert eng.name.startswith("ml/")

    # it produces grounded reports through the same harness
    rep = evaluate("ml")
    assert rep.accuracy >= 0.9
    assert rep.groundedness_mean >= 0.99

    # and never cites evidence outside the bundle
    b, _ = build_bundle(SCENARIOS[0])
    tr = reconstruct(b, engine=eng, save=False)
    valid = ({r.id for r in b.readings} | {m.id for m in b.maintenance}
             | {e.id for e in b.mes})
    assert tr.report.cited_ids() <= valid
