# Replay & Determinism (proving a change is safe before it ships)

## What it is

**Deterministic replay** is re-running a system on the *exact inputs it saw
before* and getting a result that depends only on the code under test — not on
anything that changed in the environment since. If you captured what the system
saw, you can feed a *new version* the same inputs and attribute every output
difference to your change and nothing else.

**Determinism** is the precondition: `f(x)` must always return the same value for
the same `x`. A function is deterministic when it's a *pure function* of its
inputs — no hidden reads of a database, a clock, a random seed, the network, or
global state.

**Regression testing for AI systems** is the payoff: instead of eyeballing a few
examples after a change, you replay a whole corpus of captured runs through the
candidate and compute a diff — what got *fixed*, what *regressed*, what's
unchanged — and turn that into a **SHIP / HOLD** decision.

## Why it matters

Iterating on an AI system is terrifying without this. You "improve" a prompt or a
rule and you have no idea what you quietly broke — the change that fixes case A
might regress case B, and with a stochastic model you can't even reproduce the
old behaviour to compare. Replay converts "I think this is better" into "this
fixes 6, regresses 0, therefore SHIP." For an FDE shipping changes into a
customer's live system, that gate is the difference between confident iteration
and praying. This is arguably the single most important idea in the whole
project — the README leads with it.

## How THIS project uses it

### Purity is the enabler

The reconstruction engine is a **pure function of an `EvidenceBundle`**:
`engine.reconstruct(bundle) -> (IncidentReport, [ToolCall])`. It reads only the
bundle — never the store, never the clock (`fie/models.py` docstring;
`docs/architecture.md` "Key idea: the engine is pure"). This is what everything
below rests on. If the engine peeked at the live database or `datetime.now()`
inside its classification, replay would be meaningless — the result could move for
reasons unrelated to your code change.

Look at `fie/agent/engine.py:RuleBasedEngine._classify` — it's branches over
`SignalStats` derived purely from the bundle. No I/O. Same bundle in → same
category out, forever.

### Trace capture (snapshotting the inputs)

Every reconstruction is captured into a `RunTrace` (`fie/models.py`), and the
crucial field is `inputs: EvidenceBundle` — **the exact evidence the engine saw**.
The model's own comment: "Snapshotting these is what makes replay deterministic
and independent of later changes to the store." `fie/agent/reconstruct.py`
assembles the trace (run_id, engine, prompt_version, `inputs=bundle`,
`tool_calls`, `report`) and `save_trace` writes it to `data/runs/RUN-*.json` as
inspectable, git-friendly JSON.

So a trace is a self-contained, replayable record: the inputs + what the agent did
(tool calls) + what it concluded (report) + provenance (which engine/prompt
version produced it).

### Replay

`fie/replay/replay.py`:

- `capture_baseline(engine_name)` runs an engine over the golden bundles and saves
  the traces — these "stand in for production traces."
- `replay_trace(trace, new_engine_name)` is the heart of it:

```python
def replay_trace(trace, new_engine_name):
    engine = get_engine(new_engine_name)
    return reconstruct(trace.inputs, engine=engine, save=False)  # trace.inputs, NOT the store
```

It feeds `trace.inputs` (the snapshot) — *never the live store* — to the new
engine. The docstring nails the guarantee: "Deterministic: uses `trace.inputs`
(never the live store), so the result depends only on the candidate engine, not on
data that changed since."

### Regression report + SHIP/HOLD gate

`fie/replay/regression.py:run_regression(baseline, candidate)`:

1. captures baseline traces over the golden cases;
2. replays each through the candidate;
3. classifies every case into a status by comparing old vs new correctness:
   `fixed` (was wrong, now right), `regressed` (was right, now wrong),
   `unchanged_correct`, `unchanged_wrong`, or `changed`;
4. computes the verdict:

```python
verdict = "SHIP" if regressed == 0 and new_acc >= old_acc else "HOLD"
```

**Ship only if zero regressions and accuracy didn't drop.** The headline result
(`README.md`, `docs/failure-model.md`):

```
rule-based/1.1.0 -> rule-based/1.2.0:  accuracy 62% -> 100% | fixed 6, regressed 0  => SHIP
```

Reverse the arguments (candidate = the buggy engine) and the *same machinery*
returns `HOLD` with 6 regressions. As the docs put it: "The verdict — not the
diagnosis — is the deliverable."

## Deeper mental model

Three properties compose into the guarantee, and you should be able to name each:

1. **Purity** (`reconstruct(bundle)` reads only `bundle`) → determinism.
2. **Input capture** (`RunTrace.inputs`) → replayability (you can reconstruct the
   exact `x`).
3. **Provenance** (engine/prompt/agent version on both the report and the trace)
   → attributability (you know *which* code produced each result).

Together: `old_output = f_old(x)`, `new_output = f_new(x)`, and because `x` is
frozen and `f` is pure, `old_output ≠ new_output ⟹ f_old ≠ f_new`. The diff is
*caused by the code change*. That's the entire logical basis for a trustworthy
SHIP/HOLD call.

Contrast with how people usually "test" AI changes: run the new version on *fresh*
data and compare to a *memory* of the old behaviour. That confounds three
variables (code change, data change, model nondeterminism) and proves nothing.
Replay holds two of them fixed so the third is isolated.

The LLM wrinkle (be honest about it): hosted LLMs aren't perfectly deterministic
even at temperature 0, so an LLM engine's replay isn't bit-reproducible. That's
*why the default engine is the deterministic rule engine* and why the
guarantees are demonstrated with it. For LLM engines you'd shift from
exact-match replay to *distributional* regression (run N times, compare metric
distributions) — but the architecture (captured inputs, versioned provenance,
diff-and-gate) is identical.

## Common interview questions with strong answers

**Q: What makes deterministic replay possible here?**
Two things: the engine is a pure function of an `EvidenceBundle` (no store, no
clock), and every run snapshots that bundle into `RunTrace.inputs`. Replay feeds
the snapshot — not the live store — to a new engine, so any output difference is
attributable purely to the engine change. See `replay_trace` in
`fie/replay/replay.py`.

**Q: Why snapshot the inputs instead of re-querying the store?**
Because the store changes. New data arrives, old data gets corrected. If you
re-queried, a "regression" might actually be a data change, and you could never
prove your fix caused the improvement. Snapshotting freezes `x` so the experiment
is controlled.

**Q: How do you decide SHIP vs HOLD?**
`SHIP` iff `regressed == 0 and new_accuracy >= old_accuracy`
(`regression.py:run_regression`). Fixing bugs is good; introducing *any*
regression blocks the ship. It's asymmetric on purpose — the cost of a regression
in production usually dwarfs the benefit of one more fix.

**Q: How is this different from a normal unit test?**
Unit tests assert fixed expected outputs for a few hand-written inputs. Replay
regression compares *two versions* of the system over a *captured corpus* and
reports a fix/regress diff — it's differential and corpus-scale. It answers "did
my change make things net better without breaking anything," which point-wise
asserts can't.

**Q: Your engine can be an LLM, which isn't deterministic. Doesn't that break
replay?**
It breaks *bit-exact* replay, yes — which is exactly why the default and the
demonstrated guarantees use the deterministic rule engine, and why every LLM path
falls back to it. For LLM engines you keep the same machinery but switch the
comparison from exact-match to distributional (run repeatedly, compare metric
distributions, gate on statistical regression). Captured inputs and versioned
provenance still do the heavy lifting.

**Q: What is a `RunTrace` and why does it exist?**
A self-contained record of one reconstruction: the exact inputs, the tool calls,
the report, and the engine/prompt versions (`fie/models.py`). It exists so runs
are replayable, auditable, and diffable — it's the unit of both replay and
observability.

**Q: Where could non-determinism sneak in and how do you prevent it?**
Clocks (`generated_at` is stamped at the orchestrator edge, never used in
classification), the store (bundle is snapshotted), randomness (rules have none;
LLM uses temperature=0), and dependency versions (pinned; feature contract
checked in `MLEngine` to catch train/serve skew). You audit the pure core for any
hidden read of mutable global state.

## Resources to learn more

- **"Record and replay" debugging (e.g. Mozilla `rr`)** — the same principle at
  the systems level: capture nondeterministic inputs, replay deterministically.
- **Martin Fowler: "What is a Deterministic / Pure Function" and event-sourcing
  write-ups** — snapshotting inputs to replay state is the event-sourcing idea.
- **"Hidden Technical Debt in Machine Learning Systems"** (Sculley et al., NeurIPS
  2015) — why reproducibility and versioning matter in ML.
- **Google "Rules of Machine Learning" (Martin Zinkevich)** — practical guidance
  on train/serve consistency and safe iteration.
- **Shadow/canary deployment and A/B testing literature** — the production-scale
  cousins of offline replay-and-gate.
