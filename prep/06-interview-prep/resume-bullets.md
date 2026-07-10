# Resume bullets

Location: `prep/06-interview-prep/resume-bullets.md`. Tuned for a Forward
Deployed / ML-systems role (e.g. Lunar). Numbers are embedded for ATS + impact;
wording is kept plain so it reads human, not generated.

## Project header line (keep — it's keyword-dense for ATS)
**Factory Incident Engine** — Python, scikit-learn, pandas, SQLite, pytest, GitHub Actions CI
· github.com/lokendra005/factory-incident-engine

## The three bullets (primary — with numbers)

- Built a Python ingestion pipeline that processes ~4,800 machine-telemetry
  records per run and routes malformed, duplicate, and out-of-range rows (~3% of
  input) to a dead-letter queue, with checkpointed exactly-once recovery proven
  by an automated crash test.

- Wrote an evaluation and replay harness that regression-tests the diagnosis
  logic against a 16-case labeled set; used it to catch a root-cause
  misclassification and verify the fix with deterministic replay — 6 of 6 failing
  cases corrected, 0 regressions.

- Trained a scikit-learn classifier on 876K rows of real Microsoft Azure
  predictive-maintenance data, reaching 0.90 macro-F1 across 4 failure types, and
  made the rule-based, ML, and LLM backends interchangeable; 49 tests, CI on
  Python 3.11 and 3.12.

## Compact variant (2 lines each, if space is tight)

- Built a fault-tolerant Python data pipeline (idempotent ingest, dead-letter
  queue, crash-safe checkpoints) processing ~4,800 records/run at ~3% rejection.

- Added an eval + replay harness that caught a diagnosis bug and verified the fix
  (6 of 6 cases corrected, 0 regressions) before release.

- Trained a scikit-learn model on 876K rows of real Azure predictive-maintenance
  data (0.90 macro-F1); 49 tests, CI on Python 3.11/3.12.

## Keyword-dense variant (if the JD is heavy on specific terms)

- Designed an ETL/ingestion pipeline in Python (validation, deduplication,
  dead-letter queue, schema-drift detection, checkpointing) over a normalized
  SQLite store, handling ~4,800 records/run.

- Built an offline evaluation harness (correctness, groundedness, tool-use) and
  deterministic replay for regression testing an ML/LLM agent; caught a
  misclassification bug and fixed all 6 failing cases with no regressions.

- Trained and benchmarked scikit-learn classifiers on 876K rows of real
  predictive-maintenance telemetry (0.90 macro-F1); integrated rule-based, ML,
  and LLM (Grok) backends; pytest + GitHub Actions CI.

## Numbers you can use (all real, all defensible)
| number | means |
|---|---|
| ~4,800 records/run | raw telemetry lines ingested per demo run |
| ~3% dead-lettered | 149 of 4,807 rows rejected with a reason (nothing silently dropped) |
| 6 of 6 fixed, 0 regressions | the fix corrected every known failing case (use this, not "100%") |
| 0.90 macro-F1 | **the credible headline** — real Azure data, not a perfect score |
| 876K rows | real Azure PdM telemetry the ML pipeline trained on |
| 0.90 macro-F1 | component-failure classification on that real data |
| 4 failure types | components comp1–4 in Azure PdM |
| 49 tests / 3.11 & 3.12 | test suite + CI matrix |
| 3 backends | rule-based, ML, LLM — one interchangeable contract |

## About the "100%" — don't put it on the resume
The rule engine scores 100% on the 16-case golden set, but a bare "100% accuracy"
reads as *made up* or *trivial problem* to any reviewer. It's real but small,
synthetic, and cleanly separable — an in-distribution regression test, not
real-world performance. So on the resume use **"6 of 6 failing cases fixed, 0
regressions"** (concrete, believable) and let **0.90 macro-F1 on 876K real rows**
be your headline metric.

If asked in person, justify it honestly:
> "The 100% is on a small deterministic golden set I built to unit-test the
> engine — it's a controlled regression check, not a benchmark. What matters is
> the harness caught a real bug and replay proved the fix corrected all six
> failing cases with no regressions. For real-world numbers I trained on the
> Azure PdM benchmark, 876K rows, 0.90 macro-F1."

That framing shows you know the difference between in-distribution testing and
generalization — which reads as senior, not as inflating a number.

## Making it NOT look AI-generated
- **Vary the structure.** Don't start all three bullets the same way or make them
  the same length — the compact variant above is intentionally uneven. Rewrite one
  in your own words so your voice shows.
- **Keep at least one hard number per bullet.** Specific numbers read human;
  vague impact ("significantly improved") reads generated.
- **Cut hype words** that scream AI: leveraged, spearheaded, robust, seamless,
  cutting-edge, state-of-the-art, utilized, orchestrated. The bullets above use
  plain verbs (built, wrote, trained, added, caught).
- **Use concrete domain nouns** (dead-letter queue, checkpoint, macro-F1,
  Azure PdM) — generated bullets stay generic; real ones name things.
- **Match the JD's exact terms** where they're true (data pipeline / evaluation /
  observability / agents) instead of inventing synonyms.
- **Formatting for ATS:** plain bullets, no tables/columns/text-boxes/icons in the
  actual resume (ATS can't parse those). Put the tech-stack keywords in the header
  line and in the bullets naturally. Save/submit as `.docx` or a text-based PDF,
  not an exported image.

## Honesty guardrail
"Production-style," not "production." The data is simulated for reproducible
ground truth; the ML numbers (876K rows, 0.90 macro-F1) are on the **real** Azure
PdM benchmark. Don't imply a live plant deployment. See `weaknesses-and-honest-answers.md`.
