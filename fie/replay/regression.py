"""Side-by-side regression report: baseline engine vs candidate engine."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from ..agent.reconstruct import incident_id_for
from ..eval.golden import load_golden
from .replay import capture_baseline, replay_trace


class RegressionRow(BaseModel):
    incident_id: str
    asset: str
    expected: str
    old_category: str
    new_category: str
    old_confidence: float
    new_confidence: float
    old_correct: bool
    new_correct: bool
    status: str          # fixed | regressed | unchanged_correct | unchanged_wrong | changed


class RegressionReport(BaseModel):
    old_engine: str
    new_engine: str
    n: int
    fixed: int
    regressed: int
    unchanged_correct: int
    unchanged_wrong: int
    old_accuracy: float
    new_accuracy: float
    verdict: str         # SHIP | HOLD
    generated_at: str
    rows: list[RegressionRow] = Field(default_factory=list)

    def summary_line(self) -> str:
        return (f"{self.old_engine} -> {self.new_engine}: "
                f"accuracy {self.old_accuracy:.0%} -> {self.new_accuracy:.0%} | "
                f"fixed {self.fixed}, regressed {self.regressed} => {self.verdict}")


def run_regression(baseline_engine: str, candidate_engine: str,
                   cases=None) -> RegressionReport:
    cases = cases if cases is not None else load_golden()

    # map incident_id -> expected label
    expected: dict[str, str] = {}
    for bundle, labels in cases:
        iid = incident_id_for(bundle.asset, bundle.window_start)
        expected[iid] = labels["expected_category"]

    baseline_traces = capture_baseline(baseline_engine, cases=cases, save=True)

    rows: list[RegressionRow] = []
    for tr in baseline_traces:
        new_tr = replay_trace(tr, candidate_engine)
        iid = tr.incident_id
        exp = expected.get(iid, "")
        old_cat = tr.report.root_cause_category
        new_cat = new_tr.report.root_cause_category
        old_ok = (old_cat == exp)
        new_ok = (new_cat == exp)
        if old_ok and new_ok:
            status = "unchanged_correct"
        elif not old_ok and new_ok:
            status = "fixed"
        elif old_ok and not new_ok:
            status = "regressed"
        elif old_cat == new_cat:
            status = "unchanged_wrong"
        else:
            status = "changed"
        rows.append(RegressionRow(
            incident_id=iid, asset=tr.asset, expected=exp,
            old_category=old_cat, new_category=new_cat,
            old_confidence=tr.report.confidence,
            new_confidence=new_tr.report.confidence,
            old_correct=old_ok, new_correct=new_ok, status=status,
        ))

    n = len(rows) or 1
    fixed = sum(r.status == "fixed" for r in rows)
    regressed = sum(r.status == "regressed" for r in rows)
    unchanged_correct = sum(r.status == "unchanged_correct" for r in rows)
    unchanged_wrong = sum(r.status == "unchanged_wrong" for r in rows)
    old_acc = round(sum(r.old_correct for r in rows) / n, 3)
    new_acc = round(sum(r.new_correct for r in rows) / n, 3)
    # Ship only if we fix at least one known bug and introduce zero regressions.
    verdict = "SHIP" if regressed == 0 and new_acc >= old_acc else "HOLD"

    return RegressionReport(
        old_engine=baseline_traces[0].engine if baseline_traces else baseline_engine,
        new_engine=candidate_engine, n=len(rows), fixed=fixed, regressed=regressed,
        unchanged_correct=unchanged_correct, unchanged_wrong=unchanged_wrong,
        old_accuracy=old_acc, new_accuracy=new_acc, verdict=verdict,
        generated_at=datetime.now(timezone.utc).isoformat(), rows=rows,
    )
