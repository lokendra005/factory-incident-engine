# Incident Reconstruction & Root-Cause Analysis in a Plant

## What it is

When a machine trips, scraps a batch, or goes down, someone has to answer three
questions before the line restarts: **what happened, in what order, and why?**
That is *incident reconstruction* (building the timeline from evidence) feeding
into *root-cause analysis* (RCA — establishing the true underlying cause, not
just the symptom). In a plant this is often a formal, documented process because
the answer drives money (scrapped parts, downtime), safety, and whether the same
failure recurs next shift.

Reconstruction is the forensic half: gather telemetry, alarms, operator actions,
and maintenance history for the window; align them on one clock; and produce a
defensible narrative. RCA is the reasoning half: apply a method (5-whys,
fishbone, fault tree) to get from symptom to cause, and — critically — to state
your **confidence** and what evidence is **missing**.

## Why it matters

For an FDE at a manufacturing-AI company, "reconstruct the incident and tell me
why" is the flagship use case. Plant staff already do this manually and slowly;
an AI that does it faster, consistently, and *with citations* is the product. But
the domain punishes overconfidence: a wrong root cause sends a technician to
replace the wrong part, or worse, tells someone to ignore a real fault. So the
bar is not "guess the most likely cause" — it is "produce a **grounded**
diagnosis with calibrated confidence, and abstain when the evidence isn't there."
That posture is what separates a demo from something a plant will actually trust.

## How THIS project frames it

FIE treats reconstruction as a **pure function**:
`EvidenceBundle → (IncidentReport, tool_calls)`. That framing (documented in
`fie/models.py`) is the backbone of the whole design.

### The evidence bundle (the inputs)

`reconstruct_from_store` (`fie/agent/reconstruct.py`) assembles everything
relevant to an asset + time window:

- **telemetry** readings in the window (`Store.query_readings`);
- **maintenance** records with a look-back (default 120 days — because a bearing
  replaced 18 months ago or a deferred sensor calibration is causal context);
- **MES events** in the window (error codes, shutdowns, config changes);
- **prior incidents** on the same asset (for "have we seen this before?").

Snapshotting these inputs into the run trace is what makes the whole thing
**replayable**: a new engine version can be re-run against the exact bytes the old
one saw (`RunTrace.inputs` in `fie/models.py`).

### The incident report (the output)

`IncidentReport` (`fie/models.py`) is the reconstruction deliverable, and its
shape *is* the RCA discipline:

- `root_cause` (prose) + `root_cause_category` (one of eight typed categories) +
  `confidence` (0–1);
- `timeline: list[TimelineEntry]` — the reconstructed sequence, each entry
  timestamped, severity-tagged (info/warn/critical), and carrying `evidence_ids`;
- `supporting_evidence: list[Evidence]` — **every claim must resolve to a cited
  record id** (telemetry / maintenance / mes);
- `missing_evidence: list[str]` — what would raise confidence but isn't present;
- `recommended_actions` — what to do next;
- `similar_incidents` — prior matches;
- provenance: `engine`, `agent_version`, `prompt_version`, `data_reliability`,
  and `blocked` / `blocked_reason` for the gate.

### Building the timeline

`build_timeline` in `fie/agent/engine.py` is the reconstruction proper. It walks
the "interesting" signals, and for each one that deviated meaningfully from
baseline (≥25% change with a detected first-anomaly timestamp) it emits a
timeline entry — "Coolant flow fell from 28 to 6" — carrying the ids of the exact
readings that prove it. It then interleaves MES events (a shutdown is
`critical`, an error_code or config_change is `warn`) and **sorts everything by
timestamp**. The result is a single, evidence-anchored chronology across three
data sources on one clock — which is precisely what a human reconstructor draws
on a whiteboard.

### From timeline to root cause

The `_classify` method (`fie/agent/engine.py`) is the RCA engine. It reduces each
signal to a few features (temp rose? coolant dropped? load pinned? vibration up?
defect high? was there a config change?) and applies **corroboration rules**:

```
if load_high and temp_rise:            overload        (load is the driver)
if coolant_drop and temp_rise:         cooling_degradation (correlated pair)
if temp_rise and not coolant_drop and not load_high:  sensor_fault (no driver ⇒ instrument)
if vib_rise and not has_config:        bearing_wear
if has_config and (defect_high or vib_rise):  operator_config
if defect_high and not has_config:     tool_wear
```

This is 5-whys compressed into physics: *temp rose → why? → because load was
pinned → why? → because feed override was applied.* Each "why" is answered by a
different signal or MES event, not by restating the symptom.

### Confidence and missing evidence — the honesty layer

Two mechanisms keep FIE from overclaiming:

1. **The reliability gate** (`fie/reliability.py`). Before the engine runs,
   `assess()` scores telemetry coverage for the window: expected frames vs
   observed, largest internal gap, staleness. If telemetry reliability is below
   `GATE_MIN_SCORE = 0.70`, reconstruction is **blocked** — the report comes back
   `blocked=True`, category `unknown`, confidence ≈ `0.2 * reliability`, with
   `missing_evidence` explaining exactly how many frames were absent. This is the
   `_insufficient` scenario: 72% of the window missing ⇒ abstain, don't guess.
2. **Confidence scaling.** Even when not blocked, `confidence = base_conf *
   reliability` (`fie/agent/engine.py`), so partial data yields a lower number.
   And `_missing_evidence` adds category-specific gaps — e.g. for `sensor_fault`,
   "Sensor calibration record to confirm the fault"; for `bearing_wear`, "Bearing
   vibration spectrum / FFT for confirmation."

### Groundedness — no hallucinated evidence

The evaluator `groundedness` (`fie/eval/evaluators.py`) scores what fraction of
cited ids actually resolve to records in the bundle, blended with whether the
*key* signals for that category were cited. A report that cites an id not present
is hallucinating and is penalized hard. The test `test_every_cited_id_resolves`
(`tests/test_agent.py`) asserts `report.cited_ids() <= valid` for every scenario —
citations are a hard invariant, not a nicety. For an FDE this is the line that
makes RCA trustworthy: *every* sentence traces to a record you can open.

## RCA methods (the vocabulary to defend)

- **5-Whys** — ask "why" iteratively until you reach an actionable root cause,
  not a symptom. Cheap, great for single-chain causes; weak for multi-factor
  failures. FIE's classification chain is essentially a physics-guided 5-whys.
- **Fishbone / Ishikawa diagram** — categorize candidate causes into buckets
  (the "6 Ms": Machine, Method, Material, Measurement, Man/people,
  Milieu/environment). FIE's eight categories map cleanly: bearing_wear/overload
  = Machine, tool_wear = Material/tooling, operator_config = Method/Man,
  sensor_fault = Measurement.
- **Fault Tree Analysis (FTA)** — top-down boolean logic from a top event to
  contributing basic events. Good for safety and multi-cause interactions.
- **Pareto analysis** — 80/20 on failure frequency to prioritize which causes to
  fix first (across many incidents, not one).
- **FMEA** — proactive, not forensic: rank failure modes by severity ×
  occurrence × detectability *before* they happen. Complements RCA.
- **8D** — a formal corrective-action framework (common in automotive) that wraps
  RCA in containment, verification, and prevention steps.

The point to make: FIE is **automated reconstruction + a physics-informed
5-whys**, with the fishbone categories as its output taxonomy, and — its
distinctive contribution — an explicit *confidence + missing-evidence +
abstention* layer that manual RCA usually leaves implicit.

## Mental model

> **Reconstruction is a courtroom, not a guess.** The timeline is the sequence of
> events; each claim needs an exhibit (a cited record); the verdict (root cause)
> must survive "what else would we expect to see if this were true?"; and if the
> evidence is too thin, you return "insufficient" rather than convict. Confidence
> is the standard of proof, and abstention is a legitimate — often the *correct* —
> outcome.

## Interview Q&A

**Q: What does incident reconstruction mean on a plant floor?** Assembling a
defensible, timestamped narrative of an event from all available evidence —
telemetry, alarms/MES, operator actions, maintenance history — aligned on one
clock, so you can then reason about cause. In FIE it's the `EvidenceBundle →
IncidentReport` function, where the timeline is built in `build_timeline` and
every entry carries the ids of the records that prove it.

**Q: How does the system avoid confidently wrong answers?** Three ways. A
data-quality gate (`fie/reliability.py`) blocks reconstruction when telemetry
coverage is below 70%, returning "unknown/blocked" instead of a guess. Confidence
is scaled by data reliability. And groundedness is enforced — every citation must
resolve to a real record, tested as a hard invariant. The insufficient-data
scenario proves the abstention path: `test_insufficient_data_is_gated_not_guessed`.

**Q: Which RCA method is this closest to?** A physics-guided 5-whys whose output
taxonomy is a fishbone (the eight categories map to Machine/Method/Material/
Measurement). The extra discipline over textbook 5-whys is corroboration: a
"why" is only accepted if a *second* signal supports it — e.g. a temp rise is only
"cooling degradation" if coolant flow actually dropped.

**Q: What is "missing evidence" and why surface it?** It's the evidence that
would raise confidence but isn't in the bundle — a coolant-pump inspection for a
cooling call, an FFT spectrum for a bearing call. Surfacing it tells the
technician what to collect next and makes the confidence number honest. FIE
generates it in `_missing_evidence` and, on gated cases, in the blocked report.

**Q: How would you handle conflicting evidence — say temp up but coolant also
up?** That fails every corroboration rule, so FIE returns `unknown` ("signature
ambiguous; insufficient corroboration") rather than forcing a category. In
practice I'd surface both signals, lower confidence, and list what would
disambiguate. Abstention beats a confident wrong call that dispatches a wrong fix.

**Q: Why capture the exact inputs into a run trace?** So a diagnosis is
reproducible and a new engine version can be judged against the *same* evidence
the old one saw — no drift from the store changing underneath you. That's what
makes the regression harness a fair, apples-to-apples comparison
(`RunTrace.inputs`, `fie/replay/`).

**Q: How do you know your reconstruction is any good?** It's scored, not
asserted. `fie/eval/evaluators.py` grades correctness, root-cause keywords,
groundedness, timeline accuracy (did we place the key MES events?), tool usage
(did we actually query the key signals?), and abstention. Those are the same
dimensions a human reviewer would grade an RCA report on.

## Resources

- ASQ (American Society for Quality) — root-cause analysis and the 7 basic
  quality tools (fishbone, Pareto, control charts).
- "Root Cause Analysis Handbook" (ABS Consulting) — practical industrial RCA.
- Kepner-Tregoe "Problem Analysis" — a rigorous structured RCA methodology.
- AIAG 8D and FMEA reference manuals — automotive-standard corrective action and
  proactive failure analysis.
- NIST/ISA on alarm management (ISA-18.2) — how plant alarms and events (the raw
  material of a timeline) are managed.
- In-repo: `fie/agent/reconstruct.py` (orchestration + gate), `fie/agent/engine.py`
  (`build_timeline`, `_classify`, confidence, missing-evidence),
  `fie/reliability.py` (the gate), `fie/eval/evaluators.py` (how RCA quality is
  scored), `docs/failure-model.md`.
</content>
