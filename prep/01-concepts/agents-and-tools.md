# Agents & Tools (function calling, the ReAct loop, and why THIS agent is a pure function)

## What it is

An **agent**, in the LLM sense, is a program that decides *what to do next* by
reasoning over evidence and then *acting* on the world through a fixed set of
**tools** (functions it is allowed to call). The loop is: observe → reason →
act → observe the result → repeat, until it can produce a final answer.

Three distinct things get lumped under "agent," and you should keep them apart
in an interview:

- **Single prompt**: one call to a model, one answer. No tools, no loop.
- **Pipeline / chain**: a fixed, hand-wired sequence of steps (extract → classify
  → format). The control flow is decided by *you*, the engineer, not the model.
- **Agent**: the *model* (or a rule engine standing in for it) chooses which
  tools to call and in what order, based on what it sees. Control flow is
  data-dependent.

A **tool** (a.k.a. *function call*) is a typed capability you expose: a name, an
argument schema, and a return value. "Function calling" is the protocol by which
a model emits a structured request to invoke one of those functions instead of
answering in prose.

## Why it matters

For a Forward Deployed Engineer this is the core competency: you are dropped into
a customer's messy environment and you have to make an LLM *do useful work over
their data safely*. The agent pattern is how you do that — you give the model
bounded, auditable actions instead of letting it free-associate. The interview
will probe whether you understand:

- why tools beat "just put everything in the prompt" (freshness, size, access
  control, auditability);
- how to keep an agent **deterministic and testable** when the underlying model
  is not;
- how to **trace** what the agent did so you can debug and evaluate it.

This project is essentially a case study in answering those three questions.

## How THIS project uses it

The key design decision — and the thing to lead with — is that **the
reconstruction engine is a pure function of an `EvidenceBundle`**:

```
engine.reconstruct(bundle) -> (IncidentReport, [ToolCall])
```

See `fie/agent/reconstruct.py:reconstruct` (the orchestrator) and
`fie/models.py` (the `EvidenceBundle`, `IncidentReport`, `ToolCall`, `RunTrace`
models). The module docstring in `fie/models.py` states it outright: the engine
"reads **only** the `EvidenceBundle` handed to it. It never touches the store or
the clock."

### The toolbox

The tools live in `fie/agent/tools.py` as the `Toolbox` class, constructed over a
single bundle (`Toolbox.__init__(self, bundle)`). It exposes three real tools:

- `Toolbox.query_telemetry(signal)` — returns a `SignalStats` (baseline, end,
  delta, max_jump, first anomaly, and the evidence ids that best show the
  signature) for one signal.
- `Toolbox.search_maintenance(keyword)` — returns matching maintenance records.
- `Toolbox.find_similar_incidents(category)` — returns prior incidents of the
  same root-cause category (a tiny structured "retrieval" step).

There is also `Toolbox.mes_events()`, deliberately *not* framed as a tool call
(it's a convenience accessor, not a decision the agent "makes").

### Every tool call is captured

Look at the top of each method in `tools.py`: they append a `ToolCall` to
`self.calls`. For example, `query_telemetry` records
`ToolCall(name="query_telemetry", args={"signal": signal}, result_count=len(rows),
result_ids=ev_ids[:3], note=...)`. The docstring of `tools.py` explains the
intent directly: "Every tool call is recorded (name, args, result count, a sample
of result ids) so the run trace is a faithful, replayable record of what the
agent looked at."

Those captured calls flow into the `RunTrace.tool_calls` field
(`fie/models.py`) via `reconstruct.py`, which is what makes two later things
possible: **replay** and **tool-usage evaluation**.

### The same toolbox for every "brain"

This is the part interviewers love. Three different engines all call the *exact
same* toolbox:

- `RuleBasedEngine.reconstruct` in `fie/agent/engine.py` — deterministic
  if/else classifier (the default).
- `LLMEngine.reconstruct` in `fie/agent/llm.py` — Claude or Grok, via
  `_summarize(tb)` which itself calls `tb.query_telemetry(...)`,
  `tb.search_maintenance(...)`, `tb.mes_events()`.
- `MLEngine.reconstruct` in `fie/agent/ml_engine.py` — a trained RandomForest,
  which *also* runs the tool calls (even though it predicts from a feature
  vector) so that "tool-usage evaluation is meaningful for ML too" (its comment).

Because the interface is identical, the evaluation harness can score all three
on the same axis (`fie/eval/evaluators.py:tool_usage` checks that
`query_telemetry` was called for each key signal). The "agent" abstraction here
is the *contract*, not a specific model.

### Where's the ReAct loop?

Honest answer for the interview: this project uses the ReAct *pattern* but
compresses the loop. In a classic ReAct agent, the model interleaves
Thought/Action/Observation turns, calling tools one at a time and deciding the
next call from the last observation. Here, the tool calls are made up-front to
build a compact evidence summary (`LLMEngine._summarize`), and the model does a
single reasoning step over that summary. That is a deliberate trade: for a
narrow, well-understood diagnostic task, a fixed tool sweep + one grounded
reasoning step is more *deterministic and cheaper* than a free multi-turn loop,
and it still preserves the ReAct virtues (tool-grounded, traced, cite-only). If
the task space were open-ended, you'd open the loop up.

## Deeper mental model

Think of the agent as three separable concerns:

1. **The world model / evidence** — `EvidenceBundle`. Immutable, snapshotted,
   the *only* input.
2. **The action surface** — `Toolbox`. Typed, capture-instrumented, shared.
3. **The policy** — the engine (`RuleBasedEngine` / `LLMEngine` / `MLEngine`).
   The only part that varies. It maps evidence + tool results → an
   `IncidentReport`.

Purity is the load-bearing property. Because `reconstruct(bundle)` depends on
nothing but its argument (no DB reads, no `datetime.now()` inside the classifier
— note `generated_at` is stamped at the edge, and the *classification* logic in
`_classify` never reads the clock), the function is **referentially
transparent**: same bundle in, same report out. Everything downstream —
reproducible eval, deterministic replay, regression gating — is a free
consequence of that one decision.

The tool-capture pattern is the agent-world analogue of **structured logging /
spans**. A `ToolCall` is a span: name, args, result cardinality, a sample of the
ids touched. You are not logging free text; you are logging a machine-checkable
record of what the policy looked at, which is exactly what you need to later ask
"did it look at the coolant signal before blaming cooling?"

## Common interview questions with strong answers

**Q: What actually makes something an "agent" versus a pipeline?**
Who decides control flow. In a pipeline the engineer hard-codes the step order.
In an agent the policy (model or rules) chooses actions based on observations. In
this project the boundary is soft on purpose: the tool sweep is fixed, but the
*classification decision* is data-dependent (`_classify` branches on which
signatures corroborate), and with an LLM backend the model chooses which
evidence ids to cite. The value is in the tool contract + tracing, not in
maximizing loop-iterations.

**Q: Why capture tool calls at all?**
Three reasons: (1) **debuggability** — you can see exactly what the agent
examined; (2) **evaluation** — `tool_usage` in `evaluators.py` grades whether it
queried the signals that matter, catching an agent that guessed the right answer
without looking; (3) **replay/regression** — the trace is a faithful record so a
new engine can be diffed against it.

**Q: Why is the engine a pure function, and what would break if it weren't?**
Purity gives reproducible eval and deterministic replay. If the engine read the
live store or the wall clock, a replay would no longer be attributable to the
engine change — data drift or time-of-day could move the result, so you could
never prove a fix is *the* cause of an improvement. See the docstring in
`fie/models.py` and `docs/architecture.md` ("Key idea: the engine is pure").

**Q: How do you make an agent testable when the model is nondeterministic?**
Push nondeterminism to the edges. Here the LLM backends run at
`temperature=0` (`GrokEngine._complete`), any failure falls back to the
deterministic rule engine, and tests/CI never hit the network. The *default*
policy is deterministic rules, so the whole eval/replay machinery is exercisable
with zero external dependencies.

**Q: Single prompt vs agent — when would you NOT build an agent?**
When the task fits in one prompt and the data is small, fresh, and public, a
single call is simpler and cheaper — don't add a loop for ceremony. You reach for
tools when data is too big for context, changes faster than you can re-prompt,
needs access control, or when you need an *audit trail* of what was consulted.
This project needs the last two, so tools are justified.

**Q: How would you add a genuinely new tool safely?**
Add a method on `Toolbox` that (a) reads only from `self.bundle`, (b) appends a
`ToolCall` describing what it did, and (c) returns typed data. Then it's
automatically traced, replayable, and evaluable. The contract does the work.

## Resources to learn more

- **ReAct: Synergizing Reasoning and Acting in Language Models** (Yao et al.,
  2022) — the paper that named the pattern.
- **Anthropic engineering blog: "Building effective agents"** — a practical
  taxonomy of workflows vs agents; strongly aligned with this project's "prefer
  the simplest thing that works" stance.
- **Anthropic docs: Tool use / function calling** — the concrete request/response
  shape of a tool call.
- **OpenAI docs: Function calling guide** — the same concept in the
  OpenAI-compatible API used by the Grok backend here.
- **"The Rise and Potential of Large Language Model Based Agents: A Survey"**
  (Xi et al., 2023) — a broad map of the agent design space.
