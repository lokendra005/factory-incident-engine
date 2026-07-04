"""Evaluation harness: run an engine over the golden set and score it."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from ..agent.engine import get_engine
from ..agent.reconstruct import reconstruct
from . import evaluators as ev
from .golden import load_golden


class CaseResult(BaseModel):
    key: str
    asset: str
    expected_category: str
    got_category: str
    correct: bool
    keywords_ok: bool
    groundedness: float
    timeline: float
    tool_usage: float
    abstention_ok: bool
    confidence: float
    blocked: bool
    passed: bool
    incident_id: str


class EvalReport(BaseModel):
    engine: str
    prompt_version: str
    n: int
    accuracy: float
    groundedness_mean: float
    timeline_mean: float
    tool_usage_mean: float
    pass_rate: float
    generated_at: str
    cases: list[CaseResult] = Field(default_factory=list)

    def failing(self) -> list[CaseResult]:
        return [c for c in self.cases if not c.passed]

    def summary_line(self) -> str:
        return (f"{self.engine}: acc={self.accuracy:.0%} "
                f"ground={self.groundedness_mean:.2f} "
                f"timeline={self.timeline_mean:.2f} "
                f"tools={self.tool_usage_mean:.2f} "
                f"pass={self.pass_rate:.0%} (n={self.n})")


PASS_GROUNDEDNESS = 0.75


def evaluate(engine_name: str | None = None, cases=None) -> EvalReport:
    engine = get_engine(engine_name)
    cases = cases if cases is not None else load_golden()

    results: list[CaseResult] = []
    for bundle, labels in cases:
        trace = reconstruct(bundle, engine=engine, save=False)
        r = trace.report
        correct = ev.correctness(r, labels)
        g = ev.groundedness(r, bundle, labels)
        t = ev.timeline_accuracy(r, bundle)
        tu = ev.tool_usage(trace, labels)
        abst = ev.abstention_ok(r, labels)
        passed = bool(correct and g >= PASS_GROUNDEDNESS and abst)
        results.append(CaseResult(
            key=labels["key"], asset=labels["asset"],
            expected_category=labels["expected_category"],
            got_category=r.root_cause_category, correct=correct,
            keywords_ok=ev.root_cause_keywords(r, labels), groundedness=g,
            timeline=t, tool_usage=tu, abstention_ok=abst, confidence=r.confidence,
            blocked=r.blocked, passed=passed, incident_id=r.incident_id,
        ))

    n = len(results) or 1
    return EvalReport(
        engine=engine.name, prompt_version=getattr(engine, "prompt_version", ""),
        n=len(results),
        accuracy=round(sum(c.correct for c in results) / n, 3),
        groundedness_mean=round(sum(c.groundedness for c in results) / n, 3),
        timeline_mean=round(sum(c.timeline for c in results) / n, 3),
        tool_usage_mean=round(sum(c.tool_usage for c in results) / n, 3),
        pass_rate=round(sum(c.passed for c in results) / n, 3),
        generated_at=datetime.now(timezone.utc).isoformat(),
        cases=results,
    )
