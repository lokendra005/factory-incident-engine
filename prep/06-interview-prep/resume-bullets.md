# Resume bullets

Tailored for a Forward Deployed / ML-systems engineering role (e.g. Lunar,
manufacturing AI). Keep the project title line + three bullets.

## Project title line
**Factory Incident Engine** — Python · scikit-learn · SQLite · pytest/CI
· [github.com/lokendra005/factory-incident-engine](https://github.com/lokendra005/factory-incident-engine)

## The three bullets (primary)

- Built a production-style manufacturing **incident-reconstruction engine** that
  ingests messy plant telemetry through a **fault-tolerant pipeline**
  (idempotent, crash-safe checkpointing, dead-letter queue, schema-drift
  detection), normalizing 4,800+ records/run behind a **data-quality gate** that
  makes the agent *abstain* on untrustworthy data instead of guessing.

- Engineered an **evaluation + deterministic-replay harness** that scores agent
  diagnoses (correctness, groundedness, tool-use) against a labeled golden set
  and replays captured run traces to gate releases — caught a real root-cause bug
  and **verified the fix with zero regressions (62%→100% accuracy)**, mirroring
  safe production deployment.

- Designed **three swappable reasoning backends** (rule-based, ML, and LLM/Grok)
  behind one contract and validated the ML pipeline on the real **Microsoft Azure
  PdM dataset (876K rows) at 0.90 macro-F1** for component-failure classification;
  49 tests, CI green on Python 3.11/3.12.

## Compact variant (if space is tight)

- Built an end-to-end **manufacturing incident-reconstruction system** (Python):
  fault-tolerant ingestion (idempotency, crash-safe checkpoints, DLQ,
  schema-drift) → normalized store → reliability-gated agent → evaluation → replay.

- Shipped an **eval + deterministic-replay loop** that caught a real diagnostic
  bug and proved the fix (**62%→100% accuracy, 0 regressions**) before release.

- Trained swappable rule/ML/LLM backends; validated the ML pipeline on the real
  **Azure PdM benchmark (876K rows, 0.90 macro-F1)**. CI on Python 3.11/3.12.

## Tailoring tips
- Mirror the job post's keywords: if it says "data pipelines / evaluation /
  observability / agents," those words are already in bullets 1–2 — keep them.
- Lead with the verb; keep each bullet ≤ 2 lines.
- The numbers to keep (all real and defensible): 62%→100%, 0 regressions,
  876K rows, 0.90 macro-F1, 49 tests, CI on 3.11/3.12.
- **Honesty:** the data is simulated (for reproducible ground truth); the ML
  numbers are on real public benchmarks (AI4I, Azure PdM). Don't imply real plant
  deployment. If asked, see `origin-story.md` / `weaknesses-and-honest-answers.md`.
