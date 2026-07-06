# Likely Questions & Strong Answers

Rapid-fire prep. Answers are short on purpose — expand only if asked.

### "Walk me through what happens to one telemetry reading."
See `05-code-walkthrough/data-flow-trace.md`. Parse → validate → deterministic id
→ idempotent upsert (dedup/conflict) → checkpoint. Bad ones dead-letter with a
reason; nothing crashes the run.

### "How do you get exactly-once with an at-least-once feed?"
I don't dedup by trying to deliver once — I make the *effect* idempotent. The id
is `sha1(machine|ts|signal)`, so a re-delivered or reprocessed line is an
`INSERT OR IGNORE` no-op. Checkpoints skip already-committed lines; a crash
between commit and checkpoint just reprocesses harmlessly. Proven in
`tests/test_ingestion.py::test_crash_midway_no_double_count`.

### "What happens when the data is bad?"
Two layers. Per-record: validation → DLQ with a machine-readable reason (never a
crash). Per-window: the reliability gate scores coverage/staleness and, below
threshold, the agent **abstains** — returns `blocked`/`unknown` instead of
guessing. Acting on untrustworthy data is the worse failure.

### "Why not just use a vector database / RAG?"
The evidence is structured, timestamped telemetry with exact identity — a SQL
range query is the right retrieval, and it's *exact* and *cheap*. Vector RAG
earns its place over unstructured text (manuals, past post-mortems, tickets).
I'd add it for `search_maintenance` over free-text notes, not for telemetry. (See
`01-concepts/rag-and-retrieval.md`.)

### "Is the agent actually an agent, or a pipeline?"
It uses tools (`query_telemetry`, `search_maintenance`, `find_similar_incidents`)
and its calls are captured in the trace. The rule engine's control flow is
deterministic; the LLM engine's is model-driven. I kept the engine a *pure
function of an evidence bundle* deliberately — that's what makes evaluation
reproducible and replay deterministic. Agent-ness is a means, not the goal.

### "How is this non-deterministic LLM output testable?"
The engine is pure over a snapshotted bundle. Replay feeds the exact captured
inputs to a candidate, so the diff is attributable to the engine change. For the
LLM path I use `temperature=0` and score with the same rule-based evaluators; an
optional LLM-as-judge exists but the deterministic judges are the source of truth
so CI stays reproducible.

### "How do you know a change is safe to ship?"
`fie regression baseline candidate`: capture the baseline's traces, replay their
inputs against the candidate, diff into fixed/regressed. SHIP only if regressed
== 0 and accuracy didn't drop. It's a regression test for the model's behavior.

### "What's groundedness and how do you enforce it?"
Every claim must cite evidence that exists. `IncidentReport.cited_ids()` must be a
subset of the bundle's record ids; the evaluator penalizes any id that doesn't
resolve, and the LLM engines *drop* invented ids before building the report. A
wrong-but-grounded answer is still possible (that's the v1.1 bug) — grounding
catches fabrication, correctness catches bad reasoning; they're separate metrics.

### "Can you train it on more data to make it better?"
Yes — `fie generate-dataset && fie train && fie eval --engine ml`. But be precise:
the LLM isn't retrained by us (better prompt/few-shot instead); the classifier
is. On clean synthetic data it ties the rule engine — the value is train/serve
parity and the same eval/replay harness, which pays off on messy real data. Full
answer in `07-extending/training-and-datasets.md`.

### "Why SQLite? Isn't that a toy?"
It's the right default for a zero-dependency, one-command demo, and the DDL is
Postgres-compatible so swapping is a connection change, not a rewrite. The
*architecture* (idempotency, DLQ, checkpoints) is production-shaped; the
*dependency* is deliberately minimal.

### "What would you do first in a real plant?"
Get real labels. Everything — rules, LLM, ML — is only as trustworthy as the
evaluation set, and the golden set is where trust comes from. Then wire OPC-UA/MQTT
connectors into the existing ingestion, which already assumes messy input.

### "Where would this break at scale?"
Batch file ingestion → move to Kafka (offset = checkpoint). SQLite write
throughput → Postgres/Timescale. Rule thresholds get brittle on many noisy
signals → that's when the ML engine earns its keep. The gate and eval/replay
concepts port unchanged.

### "What are you least happy with?"
See `weaknesses-and-honest-answers.md` — say them before they're found.
