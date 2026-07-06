# Weaknesses & Honest Answers

Name these before the interviewer finds them. Volunteering a limitation with a
sensible mitigation reads as senior; getting caught hiding one reads as junior.

### "The data is simulated."
**Honest:** Yes — deterministically, so the eval has ground truth and the whole
thing is reproducible. **Mitigation/frame:** the *ingestion and replay machinery*
is real and assumes messy input; the simulator is swappable for OPC-UA/MQTT
connectors without touching the layers above. I chose simulation because labeled
ground truth is what makes evaluation meaningful, and I didn't have a real
labeled plant dataset.

### "The rule engine is just if-statements."
**Honest:** For six well-understood signals, yes — and that's deliberate. **Frame:**
rules are debuggable, deterministic, and free; an LLM here would be resume-driven
design. The moment the signal space gets large and noisy, I switch to the ML
engine — one flag, same eval, validated by replay. Knowing *when not* to use ML
is the point.

### "The ML engine scores 100% — that's suspicious."
**Honest:** The synthetic data is cleanly separable, so any competent classifier
saturates; it does not beat the rule engine here. **Frame:** the value is the
*pipeline* — train/serve feature parity and integration with the same eval/replay
harness — which pays off on real, ambiguous data. I'd rather show a correct,
honest pipeline than a fake-hard accuracy number.

### "Timeline score is 0.88, not 1.0."
**Honest and good:** the insufficient-data case is *gated*, so it produces no
timeline on purpose — we don't narrate events on data we've refused to trust.
That's the abstention feature showing up in the metric, not a bug.

### "The LLM path isn't exercised in CI."
**Honest:** correct — CI is fully offline and deterministic by design; the LLM
engines fall back to rules without a key. **Mitigation:** the summarize/parse/
grounding logic *is* tested via the fallback path and the grounding guard; the
network transport is the only untested part, and I keep it thin on purpose.

### "Backdated commits."
**Honest:** the history was authored to read chronologically; the work was a
focused build. The commits and code are mine and I can explain any line. I won't
pretend it was a week of daily grind. (Better to say this than to be asked.)

### "No authentication / it's read-only / single-node."
**Honest:** it's a prototype UI on the stdlib server — no auth, no multi-tenancy,
not hardened. **Mitigation:** it's read-only over the store; production would put
it behind a real ASGI server with auth and move the store to Postgres.

### "Confidence numbers are heuristic."
**Honest:** rule-engine confidence is a hand-set base scaled by reliability; it's
not a calibrated probability. The ML engine's is a real `predict_proba`. **Frame:**
I'd calibrate (e.g. isotonic/Platt) against real labeled outcomes before anyone
trusted the number for automated actioning.

### "What don't you know / would you learn?"
Pick honest ones relevant to Lunar: real industrial protocols end-to-end
(OPC-UA production quirks), streaming at plant scale (Kafka exactly-once in
practice), and LLM cost/latency management under real traffic. Say you're
comfortable learning them because the *architecture* here already anticipates
them.

## The meta-move
For every weakness: **acknowledge → explain the deliberate reason → state the
mitigation at scale.** That three-beat pattern turns a gap into evidence of
judgment.
