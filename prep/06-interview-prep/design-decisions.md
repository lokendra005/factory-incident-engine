# Design Decisions & Their Defenses

For each decision: what, why, the trade-off, and what you'd do differently at
scale. Interviewers probe these — having the trade-off ready signals maturity.

### The engine is a pure function of an EvidenceBundle
**Why:** reproducible evaluation + deterministic replay. If reasoning read the
live store or the clock, you couldn't replay a past decision.
**Trade-off:** you must snapshot inputs into the trace (storage cost).
**At scale:** snapshots can be large; store references + a content-addressed blob
store instead of inlining everything.

### A data-quality gate that can veto the agent
**Why:** in a plant, a confident wrong answer on bad data causes real, physical
waste. Abstaining is the safer default; judgment > coverage.
**Trade-off:** you might block on recoverable data and frustrate users.
**At scale:** tie the threshold to a real operational policy; expose an override
with an audit log.

### Ship a real bug (v1.1) and keep it
**Why:** the honest-failure story is the most credible thing in the repo, and two
engine versions make regression testing *real* instead of hypothetical.
**Trade-off:** looks odd if unexplained ("why is there a broken engine?").
**Answer:** it's the fixture that proves the eval/replay machinery works.

### Deterministic rule engine as the default (not an LLM)
**Why:** six well-understood signals don't need an LLM; rules are debuggable,
free, offline, and deterministic. Reaching for an LLM here would be
resume-driven design.
**Trade-off:** rules get brittle as signals multiply.
**At scale:** that's exactly the boundary where I switch to the ML engine — and
it's a one-flag change, validated by replay.

### Nothing silently dropped — DLQ everything
**Why:** silent data loss is invisible until it corrupts a decision. A DLQ with
reasons is debuggable and recoverable.
**Trade-off:** DLQ can grow; needs monitoring/alerting.
**At scale:** alert on DLQ rate by reason; auto-recover known-remappable classes.

### Idempotency via a deterministic content id
**Why:** turns dedup and crash-recovery into a no-op instead of a special case.
**Trade-off:** the id must include exactly the fields that define identity; get
that wrong and you either miss dupes or drop distinct records.
**At scale:** same idea over Kafka (offset checkpoints + idempotent upsert).

### SQLite + stdlib http.server + only pydantic/jinja2
**Why:** "runs in one command, no services" is a real property reviewers value;
it proves the thing works without a setup ritual.
**Trade-off:** not the production stack.
**At scale:** Postgres/Timescale, a real ASGI server; DDL is already compatible.

### One reconstruction contract, swappable backends
**Why:** which model to use (rules/ML/LLM) is a deployment decision. The
abstraction means the eval, replay, UI, and CLI are backend-agnostic.
**Trade-off:** a shared contract constrains what any one backend can express.
**At scale:** it's a feature — you can A/B backends behind the same harness.

### LLM output constrained to strict JSON + grounding guard
**Why:** structured output is parseable and testable; dropping uncited ids stops
the model inventing evidence.
**Trade-off:** you lose some of the model's free-form nuance.
**At scale:** add a narrative field *alongside* the structured verdict.

### Feature extraction shared between train and serve
**Why:** train/serve skew is a classic silent ML bug; one function used by both
paths makes it structurally impossible, and the model artifact verifies the
feature contract.
**Trade-off:** none worth mentioning — this is just correct.

### Backdated, feature-by-feature git history
**Why:** shows incremental engineering, not a single dump.
**Be honest if asked:** the work was done in a focused burst and the history was
authored to read chronologically; the commits are real and the code is mine to
explain line by line. Don't oversell it as "a week of daily work" if it wasn't.
