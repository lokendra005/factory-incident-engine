# Data Quality & The Deployment Gate

> **Defend this:** "Half the telemetry for this machine is missing. Does your
> agent still produce a root-cause report?"
> **Answer:** "No — and that refusal is the feature."

---

## What it is

**Data quality** is the measurable fitness of data for its purpose. The classic
dimensions:

- **Completeness** — is all the expected data present? (missing frames/gaps)
- **Timeliness / freshness** — is it recent enough? (staleness)
- **Validity** — does it conform to the schema/bounds? (the DLQ handles this)
- **Accuracy** — does it reflect reality?
- **Consistency** — do sources agree?
- **Uniqueness** — no unintended duplicates? (idempotency handles this)

A **quality gate** is a rule that *blocks* a downstream action when quality falls
below a threshold. **Abstention** is the deliberate choice to return "I can't
answer this reliably" instead of a confident-but-baseless answer.

This project scores completeness + timeliness of the evidence and, below a
threshold, **blocks reconstruction entirely**. The philosophy, stated in
`fie/reliability.py:3-6`:

> "Mature engineering judgment, encoded: an agent must not act on data it cannot
> trust. We score the evidence available for an asset/window and, below a
> threshold, the gate BLOCKS reconstruction — the report comes back with
> `blocked=True` and an explanation instead of a confident-but-baseless answer."

---

## Why it matters

An LLM-driven agent will *always* produce a plausible answer — that's what makes
it dangerous. Ask it to diagnose a machine failure from 20% of the data and it
will confidently hallucinate a root cause, fully "grounded" in the handful of
readings it *does* have. In a factory, acting on that (e.g. "replace the coolant
pump") costs real money and erodes trust the first time it's wrong.

So the highest-leverage data-engineering contribution to an AI system is often
*knowing when to say nothing.* The gate converts "garbage in, confident garbage
out" into "garbage in, honest abstention out." For an FDE this is a selling
point: the system's willingness to abstain is what makes its confident answers
*trustworthy*.

---

## How THIS project implements it

The whole gate is `fie/reliability.py:assess` (lines 42-105). It takes an
`EvidenceBundle` and returns a `ReliabilityReport` with an `overall` score and a
`blocked` boolean.

### It scores the bundle, not the store — so live == replay

`reliability.py:8-10`:

> "The score is computed from the EvidenceBundle itself (not the store), so it is
> identical on the live path and the replay path."

This is a subtle, important data-engineering property: the quality assessment is a
**pure function of the same immutable input** the reasoning engine sees. Snapshot
the bundle into a run trace and you can replay the *exact* gate decision later.
Quality gating is deterministic and auditable, not dependent on mutable store
state.

### Dimension 1 — Completeness (coverage)

`reliability.py:42-49`:

```python
duration = max((end - start).total_seconds(), 0.0)
expected = int(duration // sample_seconds) + 1     # frames we SHOULD have
ts_sorted = sorted({r.ts for r in bundle.readings})
observed = len(ts_sorted)                           # distinct frames we HAVE
coverage = min(observed / expected, 1.0) if expected else 0.0
```

Expected frame count comes from the window duration divided by the sampling
interval (`config.SAMPLE_SECONDS = 60`, one frame/minute — `config.py:47-48`).
Coverage is observed/expected, capped at 1.0. This is completeness as a hard
number.

### Dimension 2 — Gap structure

`reliability.py:52-58` walks the sorted timestamps and finds the **largest
contiguous gap** in frames. This distinguishes "5% missing, scattered" (benign)
from "5% missing, all in one 30-minute blackout" (a real outage that could hide
the incident). It's reported in the detail string even though `overall` keys off
coverage×staleness.

### Dimension 3 — Timeliness (staleness)

`reliability.py:61-70`:

```python
stale = max(int((end - _iso(ts_sorted[-1])).total_seconds() // sample_seconds), 0)
...
staleness_factor = 1.0
if stale > config.STALE_SAMPLES:
    staleness_factor = max(0.0, 1.0 - (stale - config.STALE_SAMPLES) / max(expected, 1))
```

Staleness = frames between the *last* reading and the window end. If the last
reading is more than `STALE_SAMPLES` (5, `config.py:68`) frames before the window
end, the score is penalized proportionally. A feed that went dark right before the
window end is suspect even if earlier coverage was fine — the incident may have
happened *during the blackout*.

### The composite score and the gate

`reliability.py:72,90-98`:

```python
tel_score = round(max(0.0, coverage * staleness_factor), 3)
...
overall = tel_score                                 # telemetry gates deployment
blocked = overall < config.GATE_MIN_SCORE           # 0.70
```

Key decisions:

- **Telemetry is the gating source; maintenance and MES are context.** The score
  is `coverage × staleness_factor` for telemetry only (`reliability.py:90` —
  "Telemetry gates deployment; the others are context"). Maintenance/MES get a
  soft score (1.0 if present, 0.6 if absent, `reliability.py:73-74`) that informs
  but doesn't block. This reflects domain reality: you can reason about a machine
  without a maintenance record, but not without its sensor data.
- **`GATE_MIN_SCORE = 0.70`** (`config.py:64`) — below 70% trustworthy telemetry,
  the gate blocks.
- **The block carries a human-readable reason** (`reliability.py:94-99`), quoting
  the exact coverage gap and the threshold, so the abstention is *explained*, not
  a mysterious refusal.

### The block is honest, not silent

When blocked, the report says *why* (`reliability.py:96-99`):

```python
reason = (f"Telemetry reliability {overall:.0%} is below the "
          f"{config.GATE_MIN_SCORE:.0%} gate ({gap_pct:.0%} of expected "
          f"frames missing). Reconstruction blocked to avoid acting on "
          f"untrustworthy data.")
```

The `IncidentReport` model carries `blocked` and `blocked_reason` fields
(`fie/models.py:139-140`), so abstention is a *first-class output*, not an error.

### The two scenarios that exercise the gate

From `docs/failure-model.md:42-43`:

- `no_incident` — all signals nominal → the agent must **not invent a cause**.
- `unknown` — telemetry outage → the **gate blocks** → the agent must **abstain**.

The `unknown` scenario has a deliberate telemetry gap
(`_in_gap`, `generate.py:65-66,84-86`) large enough to push coverage under the
gate. Proven by `test_reliability.py`:

```python
def test_telemetry_gap_blocks_deployment():
    bundle, labels = build_bundle(_scenario("unknown"))   # has a big gap
    rep = assess(bundle)
    assert rep.blocked
    assert rep.overall < 0.7
    assert "below" in rep.reason.lower()      # tests/test_reliability.py:17-22
```

And the healthy path passes the gate:

```python
def test_full_coverage_not_blocked():
    bundle, _ = build_bundle(_scenario("cooling_degradation"))
    rep = assess(bundle)
    assert rep.overall > 0.9
    assert not rep.blocked                     # tests/test_reliability.py:10-14
```

And crucially, the score is **deterministic**
(`test_reliability.py:25-28`): the same bundle assessed twice yields identical
reports (`assess(b1).model_dump() == assess(b2).model_dump()`). Determinism is
what makes a gate defensible and auditable.

---

## Mental model / diagram

```
 EvidenceBundle (window + readings)      fie/reliability.py::assess
 ─────────────────────────────────      ───────────────────────────
        │
        ▼
   expected = duration / 60s + 1          (completeness baseline)
   observed = distinct reading timestamps
        │
        ├─► coverage      = observed / expected           (COMPLETENESS)
        ├─► largest_gap   = biggest contiguous hole       (gap structure)
        └─► staleness     = frames from last reading→end  (TIMELINESS)
        │
        ▼
   tel_score = coverage × staleness_factor
        │
        ▼
   overall = tel_score           (telemetry gates; maint/mes are context)
        │
   ┌────┴──────────────┐
 overall ≥ 0.70      overall < 0.70
   │                     │
 PROCEED             BLOCKED = True
 (agent reconstructs)   report.blocked_reason = "…70% gate…frames missing…"
                        → agent ABSTAINS with an explanation
```

The one-liner: **"I score completeness and timeliness of the telemetry as a pure
function of the evidence bundle; below 70% the gate blocks reconstruction and the
agent abstains *with a reason*. Refusing to answer on bad data is a feature, not
a failure."**

---

## Interview questions + strong answers

**Q: Why gate at all — why not let the agent try and just report low
confidence?**
A: Because LLMs produce fluent, plausible answers regardless of input quality —
low confidence gets ignored, a confident hallucination gets acted on. A hard gate
converts "confident garbage" into "honest abstention." The abstention is
structured (`blocked=True` + `blocked_reason`), so downstream systems and humans
can route it appropriately instead of trusting a baseless diagnosis. In a factory,
acting on a wrong root cause costs real money.

**Q: Which quality dimensions do you actually measure, and why those?**
A: Completeness (coverage = observed/expected frames) and timeliness (staleness =
gap between last reading and window end), plus gap structure as context.
Uniqueness and validity are handled upstream by idempotency and the DLQ, so by
the time the bundle reaches the gate those are already clean. Completeness and
timeliness are the ones that determine whether there's *enough signal in the
window* to reason about — which is exactly what the gate protects.

**Q: Why does telemetry gate but maintenance/MES don't?**
A: Domain reality. You can diagnose a machine from its sensor stream without a
maintenance log — the maintenance record is corroborating context. But you can't
diagnose anything from a sensor blackout. So telemetry coverage×staleness is the
`overall` score and the block condition; maintenance/MES get a soft 1.0/0.6 that
enriches the report without blocking it (`reliability.py:73-74,90`).

**Q: Why measure the largest *contiguous* gap, not just total missing?**
A: Because structure matters. 5% missing scattered evenly is noise; 5% missing as
one continuous blackout could be precisely the window in which the incident
occurred — the data hole could be hiding the answer. Reporting the largest gap
surfaces that risk even when overall coverage looks acceptable.

**Q: Why compute the score from the bundle instead of querying the store?**
A: Determinism and auditability. The bundle is the exact, immutable input the
reasoning engine sees; scoring it (not mutable store state) means the gate
decision is a pure function I can snapshot into a run trace and replay identically
later. `test_scores_are_deterministic` asserts the same bundle always yields the
same report — you can't defend a gate that gives different verdicts on the same
data.

**Q: Isn't a hard 0.70 threshold arbitrary?**
A: The number is tunable in one place (`config.GATE_MIN_SCORE`) and should be set
with the customer based on their risk tolerance and sampling density. What's *not*
arbitrary is having a threshold at all, and making the block explainable — the
reason string quotes the exact coverage and the gap percentage so the decision is
inspectable and the threshold can be calibrated against real outcomes.

**Q: How is this different from the DLQ?**
A: Different layer, different question. The DLQ is *per-record* validity — "is
this one reading well-formed?" The gate is *per-window aggregate* fitness — "is
there enough good data in this window to reason at all?" A window can have zero
DLQ entries (every record that arrived was valid) and still fail the gate because
too *few* records arrived. They're complementary: validity upstream, sufficiency
at the gate.

---

## Resources (real, well-known)

- **"The Six Dimensions of Data Quality"** — DAMA (Data Management Association)
  framework; the standard vocabulary of completeness/timeliness/validity/etc.
- **Great Expectations** (greatexpectations.io) — production data-quality
  validation and "data docs"; the tooling embodiment of quality gates.
- **Monte Carlo / "Data Observability"** literature — freshness, volume, and
  distribution monitoring; the modern framing of gating on data health.
- **"Designing Data-Intensive Applications"** — Kleppmann; reliability and the
  cost of acting on incomplete data.
- **Model cards / "Datasheets for Datasets"** (Gebru et al.) and the broader
  responsible-AI literature on **abstention** and knowing the limits of a model's
  competence.
- **dbt tests / dbt-expectations** — assertion-based quality gating in a modern
  ELT pipeline; directly analogous to blocking on a failed check.
