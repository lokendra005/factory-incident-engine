# Observability & Tracing (what to log in an agent system, and why)

## What it is

**Observability** is the ability to understand what a system did *from the outside*
— to answer new questions about its behaviour after the fact, without adding new
code. For a normal service that's the "three pillars": logs, metrics, traces. For
an *agent* system it means being able to reconstruct, for any given run: what
inputs it saw, what it looked at (tool calls), what it concluded, and which
version of the code/prompt produced that.

**Tracing** specifically is recording the causal path of a single request as a
tree/sequence of **spans** — timed, named, attributed units of work. In an agent,
the natural spans are **tool calls**: each is a named action with arguments and a
result. A trace is the ordered set of spans plus the final output.

Key vocabulary:

- **Span**: one unit of work (here: one tool call — its name, args, result count,
  result ids, ok/note).
- **Trace**: the full record of one run (inputs + spans + output + provenance).
- **Provenance / metadata**: which engine, prompt version, agent version, and
  timestamp produced this — so you can group and compare runs.

## Why it matters

You cannot debug, evaluate, or trust an agent you can't see inside. When a
diagnosis is wrong, "the model said so" is useless; "it queried
`spindle_temp_c` but never queried `coolant_flow_lpm`, then concluded cooling
degradation" is a root cause. For an FDE, tracing is what turns a customer's
"your agent gave a weird answer" ticket into a five-minute fix. It also *feeds
everything else*: eval reads traces to score tool-usage, and replay reads traces
to re-run inputs. Observability isn't a nice-to-have bolted on at the end here —
it's the substrate the whole quality story stands on.

## How THIS project uses it

### Every reconstruction is captured as a RunTrace

The unit of observability is `RunTrace` (`fie/models.py`), and it is captured for
*every* reconstruction in `fie/agent/reconstruct.py:reconstruct`:

```python
trace = RunTrace(
    run_id=..., incident_id=..., asset=..., window_start=..., window_end=...,
    engine=engine.name, agent_version=engine.name,
    prompt_version=getattr(engine, "prompt_version", ""),
    created_at=_now(),
    inputs=bundle,           # exact evidence seen
    tool_calls=tool_calls,   # the spans
    report=report,           # the conclusion
)
```

Notice it captures all four layers you'd want: **inputs** (`inputs`), **process**
(`tool_calls`), **output** (`report`), and **provenance**
(`engine`/`agent_version`/`prompt_version`/`created_at`). `save_trace` persists it
to `data/runs/RUN-*.json` — plain JSON, so it's git-friendly and grep-able. The
`run_id` is derived from the incident id + a hash of the engine name
(`_run_id`), so different engines over the same incident get distinct, stable
trace files.

### Tool calls are the spans

Each tool in `fie/agent/tools.py` records a `ToolCall` as it executes — this is
span emission. The `ToolCall` model (`fie/models.py`) is a compact span schema:

```python
class ToolCall(BaseModel):
    name: str                    # which action
    args: dict                   # what it was asked (e.g. {"signal": "coolant_flow_lpm"})
    result_count: int = 0        # cardinality of the result
    result_ids: list[str] = []   # a SAMPLE of ids touched (not the full payload)
    ok: bool = True              # success/failure
    note: str = ""               # human-readable detail (e.g. "baseline=.. end=..")
```

For example `query_telemetry` emits a call with `result_count=len(rows)`,
`result_ids=ev_ids[:3]`, and a `note` summarizing baseline/end/max_jump; on an
empty result it emits `ok=False, note="no readings"`. The design captures
*enough to debug and evaluate* (name, args, cardinality, a sample of ids,
outcome) without logging the full result payload — a deliberate signal-vs-noise
choice.

Crucially, **all three engines emit the same spans**: the rule engine, the LLM
engine (`_summarize` calls the toolbox), and even the ML engine — which runs the
tool calls purely so "tool-usage evaluation is meaningful for ML too"
(`ml_engine.py` comment). Consistent instrumentation across implementations is
what makes cross-engine observability and evaluation possible.

### Observability feeds eval and replay

The traces aren't write-only logs — they're consumed:

- **Eval** reads spans: `fie/eval/evaluators.py:tool_usage` inspects
  `trace.tool_calls`, pulling the `signal` arg off every `query_telemetry` call to
  check the agent queried the signals that matter. Without span capture this
  metric couldn't exist.
- **Replay** reads inputs: `fie/replay/replay.py:replay_trace` feeds
  `trace.inputs` to a new engine (see `replay-and-determinism.md`).

So the trace is simultaneously a debugging artifact, an eval input, and a replay
input. One capture, three uses.

### The gate is observable too

When the reliability gate blocks (`reconstruct.py`), the trace still gets written
— with a `blocked=True` report, the `blocked_reason`, and the coverage numbers
(`observed_frames/expected_frames`, `largest_gap_frames`). A *refusal* is a
first-class, logged outcome, not a silent skip. That matters: you can audit *why*
the agent declined, not just when it answered.

### Provenance everywhere

Both the `RunTrace` and the `IncidentReport` (`fie/models.py`) carry
`engine`, `agent_version`, and `prompt_version`. When an LLM engine falls back,
the name is rewritten to `"claude/... (fell back to rule-based/1.2.0)"`
(`llm.py`) — so the trace records not just *that* you got an answer but *how* it
was actually produced. That's the metadata you group by when comparing versions
or hunting a regression to a specific prompt change.

## Deeper mental model

Map the classic pillars onto this system:

| Pillar | Generic meaning | Here |
|---|---|---|
| **Traces** | causal path of one request | `RunTrace`: inputs → tool_calls → report |
| **Spans** | timed units of work | `ToolCall` per tool invocation |
| **Metrics** | aggregate numbers | `EvalReport` accuracy/groundedness/tool_usage means |
| **Metadata** | request attributes | engine / agent_version / prompt_version |

The organizing principle: **capture what you'd need to answer a question you
haven't thought of yet.** You didn't know in advance you'd want to check "did it
look at coolant before blaming cooling," but because every tool call was captured
with its args, the answer is already in the trace. That's the observability
mindset — instrument the *decisions and the inputs*, not just the final output.

Two design tensions this project resolves well:

1. **Signal vs noise.** A span stores a *sample* of result ids
   (`result_ids[:3]`, `[:5]`) and a summary `note`, not the whole result set. You
   log enough to reconstruct the decision, not so much that traces become
   unreadable or huge.
2. **Structured vs free-text.** Spans are *structured* (`name`, `args`,
   `result_count`) so they're machine-checkable — which is exactly why `tool_usage`
   can compute a metric off them. Free-text logs can't be evaluated
   programmatically. Prefer structured events for anything you'll want to query.

The deepest point for an interview: in an agent system, **observability and
evaluation and replay are the same data viewed three ways.** A trace that's rich
enough to debug is automatically rich enough to score (eval) and rich enough to
re-run (replay). Designing the trace well (pure inputs + structured spans +
provenance) gives you all three for the price of one.

## Common interview questions with strong answers

**Q: What would you log in an agent system?**
Four layers per run: (1) **inputs** — the exact evidence/context the agent saw;
(2) **process** — every tool/action as a structured span (name, args, result
cardinality, a sample of ids, ok/note); (3) **output** — the final structured
result including confidence and citations; (4) **provenance** — engine/model,
prompt version, agent version, timestamp. This project's `RunTrace` captures all
four (`fie/models.py`).

**Q: Why structured spans instead of print/log lines?**
Because you want to *query and evaluate* them, not just read them. `tool_usage`
(`evaluators.py`) computes a metric by inspecting `ToolCall.args` — impossible
with free-text logs. Structured events are machine-checkable; that's the whole
point.

**Q: How do you keep traces from becoming huge?**
Log samples and summaries, not full payloads. Each `ToolCall` stores
`result_count` plus the top few `result_ids` and a short `note`, not every row it
touched. Enough to reconstruct the decision; cheap to store and read.

**Q: How does observability connect to evaluation and replay?**
They consume the same trace. Eval reads `tool_calls` to score process quality;
replay reads `inputs` to re-run deterministically. One well-designed capture
serves debugging, scoring, and regression testing.

**Q: How do you trace a wrong answer back to a cause?**
Open the `RUN-*.json`. Check provenance (which engine/prompt), read the spans (did
it query the right signals? what were the baselines?), and read the report's
citations. The v1.1.0 bug is diagnosable exactly this way: the spans show it read
temperature and coolant, but the conclusion ignored coolant — pointing straight
at `_classify`.

**Q: Do you log when the agent declines to answer?**
Yes — a gated/blocked run still writes a full trace with `blocked=True`,
`blocked_reason`, and coverage stats (`reconstruct.py`). Refusals are
first-class, auditable events. Silent skips are an anti-pattern.

**Q: How would you scale this to production volume?**
Keep the same schema but ship spans to a real backend (OpenTelemetry →
Jaeger/Tempo/Honeycomb, or an LLM-observability tool like LangSmith/Langfuse),
sample high-volume traces while always keeping errors/blocks/regressions, and
index by the provenance fields so you can compare prompt/engine versions. The
model — structured spans + provenance + captured inputs — doesn't change; only the
transport and storage do.

## Resources to learn more

- **OpenTelemetry docs (traces & spans)** — the industry-standard model this
  project's `ToolCall`/`RunTrace` mirror informally.
- **Charity Majors et al., "Observability Engineering" (O'Reilly)** — the "ask
  new questions without new code" definition of observability.
- **Google SRE Book — chapters on monitoring & the "three pillars"** — logs,
  metrics, traces and how they relate.
- **LangSmith / Langfuse docs** — LLM-specific tracing (capturing prompts, tool
  calls, tokens, latency) if you move to a hosted backend.
- **Honeycomb's writing on high-cardinality, structured events** — why structured
  beats free-text for debugging complex systems.
