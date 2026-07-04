"""Individual evaluators. Each is a pure function of (trace, bundle, labels).

Kept deliberately simple and rule-based so the harness runs offline and its
verdicts are reproducible. An optional LLM-as-judge lives in ``llm_judge`` and
is skipped when Claude is unavailable.
"""
from __future__ import annotations

from ..models import EvidenceBundle, IncidentReport, RunTrace


def correctness(report: IncidentReport, labels: dict) -> bool:
    return report.root_cause_category == labels["expected_category"]


def root_cause_keywords(report: IncidentReport, labels: dict) -> bool:
    kws = [k.lower() for k in labels.get("expected_root_cause_kw", [])]
    if not kws:
        return True
    text = report.root_cause.lower()
    return any(k in text for k in kws)


def groundedness(report: IncidentReport, bundle: EvidenceBundle, labels: dict) -> float:
    """Fraction of cited evidence that resolves, blended with key-signal coverage.

    A report that cites ids not present in the bundle is hallucinating evidence
    and is penalized hard.
    """
    valid = ({r.id for r in bundle.readings}
             | {m.id for m in bundle.maintenance}
             | {e.id for e in bundle.mes})
    cited = report.cited_ids()
    if not cited:
        # only acceptable when there is nothing to cite
        return 1.0 if report.root_cause_category in ("no_incident", "unknown") else 0.0

    resolved = sum(1 for c in cited if c in valid)
    resolve_frac = resolved / len(cited)

    key = labels.get("key_signals", [])
    if not key:
        return round(resolve_frac, 3)

    sig_of = {r.id: r.signal for r in bundle.readings}
    cited_signals = {sig_of.get(c) for c in cited if c in sig_of}
    key_frac = sum(1 for s in key if s in cited_signals) / len(key)
    return round(0.5 * resolve_frac + 0.5 * key_frac, 3)


def timeline_accuracy(report: IncidentReport, bundle: EvidenceBundle) -> float:
    key_events = {e.ts for e in bundle.mes
                  if e.event in ("error_code", "shutdown", "config_change")}
    if not key_events:
        return 1.0
    report_ts = {t.ts for t in report.timeline}
    return round(len(key_events & report_ts) / len(key_events), 3)


def tool_usage(trace: RunTrace, labels: dict) -> float:
    key = labels.get("key_signals", [])
    if not key:
        return 1.0
    queried = {c.args.get("signal") for c in trace.tool_calls
               if c.name == "query_telemetry"}
    return round(sum(1 for s in key if s in queried) / len(key), 3)


def abstention_ok(report: IncidentReport, labels: dict) -> bool:
    """On insufficient-data cases the agent must decline, not guess."""
    if not labels.get("expects_missing_evidence"):
        return True
    return report.blocked or bool(report.missing_evidence)


def llm_judge(report: IncidentReport, bundle: EvidenceBundle):
    """Optional LLM-as-judge. Returns None (skipped) when Claude is unavailable."""
    try:
        from ..agent.claude_engine import claude_available
        if not claude_available():
            return None
        import anthropic
        client = anthropic.Anthropic()
        from .. import config
        prompt = (
            "You are grading an incident diagnosis. Given the root cause and its "
            "cited evidence summaries, answer with JSON {\"plausible\": bool, "
            "\"reason\": str}. Root cause: " + report.root_cause + "\nEvidence: "
            + "; ".join(e.summary for e in report.supporting_evidence[:8]))
        msg = client.messages.create(model=config.CLAUDE_MODEL, max_tokens=256,
                                     messages=[{"role": "user", "content": prompt}])
        import json
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception:
        return None
