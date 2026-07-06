# The Four Questions

These are the exact prompts a Forward Deployed Engineer interview will use to
find out whether you built a demo or a deployable system. Have a crisp,
specific, code-pointing answer for each. Practice saying them out loud.

---

## 1. "Show me a failure."

**Do:** Open `docs/failure-model.md` and `agent/engine.py`. Say:

> "The repo ships a real bug on purpose. Engine v1.1 blames *any* spindle-
> temperature rise on cooling degradation — here in `_classify`. On a temperature-
> *sensor* fault, where the reading climbs but coolant flow and load are perfectly
> nominal, that's wrong: it would dispatch a crew to reseal a coolant pump that
> was never the problem. And it's *confidently* wrong — it cites real temperature
> readings, so groundedness stays 1.0. That's what a dangerous production bug
> actually looks like: plausible, grounded, and wrong."

Then show the catch: `fie eval --engine rule-based/1.1.0` → 62%, with the sensor
and overload cases failing.

## 2. "Show me the evaluation dataset."

**Do:** `ls data/golden/` and open one file. Say:

> "16 labeled incidents, two per failure mode, generated deterministically from
> the scenario catalog and persisted as JSON so they're inspectable. Each file is
> the exact `EvidenceBundle` the engine sees plus the labels — expected category,
> the signals that must be cited, whether it should abstain. They're chosen to be
> *confusable on purpose*: cooling vs sensor-fault vs overload all show a
> temperature rise, so getting them right requires actual corroboration, not a
> keyword. `fie/eval/golden.py` builds them; `data-model.md` documents the shape."

## 3. "Show me the metrics."

**Do:** `fie eval` then `fie regression`. Say:

> "Five metrics, because 'right answer' isn't enough to deploy: correctness,
> groundedness (every cited id must resolve to a real record — the agent can't
> invent evidence), timeline coverage, tool-usage (did it actually query the
> signals that mattered), and abstention (does it decline on insufficient data).
> v1.2 is 100% correct, groundedness 1.0. The interesting number is the
> regression: v1.1 → v1.2 goes 62% → 100%, 6 fixed, 0 regressed, verdict SHIP —
> and if I reverse it, the same machinery says HOLD with 6 regressions. The
> verdict, not the diagnosis, is the deliverable."

## 4. "Why did you make this decision?"

Pick the decision they point at; each has a real reason (see
`design-decisions.md`). The meta-answer:

> "Every prototype choice was made so the *architecture* is real while the
> *dependencies* stay zero — it runs offline in one command. The engine is a pure
> function of an evidence bundle, which is what lets me snapshot inputs and replay
> them deterministically. The gate can veto the agent, because acting on
> untrustworthy data is worse than saying 'I don't know.' And the backend —
> rules, a trained classifier, or an LLM — is a one-flag swap, all scored by the
> same harness, because which model to use is a deployment decision, not a
> rewrite."

---

## The demo choreography (2 minutes, do this live)
```bash
make demo          # narrate the 7 stages as they scroll
fie regression     # land on: 62% -> 100%, fixed 6, regressed 0, SHIP
make serve         # open the incident page: timeline, evidence chips, confidence,
                   # reliability panel; then the regression page
```
End with: *"That loop — messy data in, a provably-safe change out — is the job."*
