# Testing with pytest in the Factory Incident Engine

## What it is

The project's test suite is written in **pytest** and lives in `tests/`. It has
**41 tests** across six files, plus a `conftest.py` that bootstraps isolation and
provides shared fixtures. The suite is fast, fully offline, and deterministic —
it never needs a network, an API key, or an external service. It is run by both
`make test` and the CI workflow.

The 41 figure is worth being precise about, because one test is parametrized:

| File | Test functions | Effective test cases |
|---|---|---|
| `test_agent.py` | 5 (one parametrized over 16 scenarios) | 20 |
| `test_ingestion.py` | 7 | 7 |
| `test_eval_replay.py` | 5 | 5 |
| `test_backends.py` | 3 | 3 |
| `test_reliability.py` | 3 | 3 |
| `test_store.py` | 3 | 3 |
| **Total** | **26 functions** | **41 collected tests** |

The parametrized `test_v12_classifies_every_scenario` expands to 16 cases (one per
golden scenario), which is what takes the collected count from 26 to 41.

## Why it matters

For an FDE, tests are how you prove an integration behaves under the *ugly*
conditions the plant will actually throw at it — duplicate deliveries, crashes
mid-ingest, renamed fields, telemetry outages — without needing the plant. This
suite is a showcase of testing the things that actually break in data pipelines
and inference systems: **determinism, idempotency, crash recovery, and correct
abstention.** Being able to say "here's how I'd test a crash mid-ingest without a
real crash" is a strong signal.

## pytest concepts used

- **Test discovery**: `pyproject.toml` sets `[tool.pytest.ini_options]` with
  `testpaths = ["tests"]` and `addopts = "-q"` (quiet). Functions named
  `test_*` in `tests/` are collected automatically.
- **Fixtures** (`conftest.py`): reusable setup injected by parameter name. FIE
  provides two — `store` (a fresh `Store` on a temp DB) and `raw_dir` (a messy
  simulated feed written to an isolated dir).
- **`tmp_path`**: pytest's built-in per-test temp directory fixture. Every FIE
  fixture builds on it, so tests never touch the real `data/` directory.
- **`@pytest.mark.parametrize`**: run one test body across many inputs with
  readable ids. `test_agent.py` parametrizes over all `SCENARIOS` with
  `ids=[s.key for s in SCENARIOS]`, so a failure names the exact scenario.
- **`assert` with messages**: plain asserts (pytest rewrites them for rich diffs),
  often with a message — e.g. `assert ... == labels["expected_category"], sc.notes`
  surfaces the scenario's own note on failure.
- **`yield` fixtures** for setup/teardown: the `store` fixture yields the store
  then calls `s.close()`.

## Test isolation — `conftest.py`

`tests/conftest.py` is the whole isolation story, and it does two things before
pytest even collects tests:

1. **Redirect all on-disk state to a temp dir** via environment variables, set at
   import time so they win before any FIE module reads config:
   ```python
   _d = tempfile.mkdtemp(prefix="fie_pytest_")
   os.environ.setdefault("FIE_DATA_DIR", _d)
   os.environ.setdefault("FIE_DB", os.path.join(_d, "plant.db"))
   os.environ.setdefault("FIE_ENGINE", "rule")
   ```
   Because `fie/config.py` reads these env vars (`os.environ.get("FIE_DATA_DIR",
   ...)`), the entire suite operates in a throwaway directory and never pollutes
   the repo's `data/`. `setdefault` means an outer override still wins.
2. **Force the deterministic offline engine** (`FIE_ENGINE=rule`), so tests never
   attempt an LLM call — reproducible and network-free by construction.

Then it defines the shared fixtures:
```python
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db"); yield s; s.close()

@pytest.fixture
def raw_dir(tmp_path):
    d = tmp_path / "raw"
    manifest = write_raw_feed(SCENARIOS, out_dir=d)
    return d, manifest
```
Each test that asks for `store` or `raw_dir` gets its own fresh, isolated copy —
`tmp_path` guarantees no cross-test contamination.

## What the tests cover, and why

### `test_agent.py` — diagnosis correctness, grounding, abstention, determinism
- `test_v12_classifies_every_scenario` (×16) — the fixed engine gets **every**
  golden scenario right. This is the core accuracy guarantee, one assertion per
  failure mode.
- `test_v11_bug_calls_sensor_fault_a_cooling_fault` — pins the *documented bug*:
  v1.1 misclassifies a sensor fault as cooling degradation. Testing a known-bad
  behavior is what makes the regression story (v1.1 → v1.2) real and provable.
- `test_every_cited_id_resolves` — grounding invariant: for every scenario,
  `report.cited_ids() <= valid` (the set of ids actually in the bundle). The
  engine can never cite evidence that doesn't exist. This is the anti-
  hallucination guardrail as a hard test.
- `test_insufficient_data_is_gated_not_guessed` — the abstention edge case: on the
  telemetry-outage scenario the report must be `blocked`, must list
  `missing_evidence`, and `confidence < 0.3`. Proves the system declines rather
  than guesses.
- `test_reconstruction_is_deterministic` — same bundle in twice → identical
  category, identical confidence, identical timeline timestamps. Determinism is a
  first-class requirement (it's what makes replay meaningful).

### `test_ingestion.py` — surviving a messy feed (7 tests)
This is the pipeline-hardening suite:
- `test_full_ingest_survives_mess` — the whole corrupted feed ingests without
  crashing, with the right defects dead-lettered.
- `test_resume_is_idempotent` — re-running ingest inserts nothing new (every
  re-seen row is a duplicate by deterministic id).
- `test_crash_midway_no_double_count` — **crash recovery**: rewind the checkpoint
  to simulate a crash mid-file, re-drive, and assert the row count is unchanged.
  This tests exactly-once *effect* under at-least-once delivery without needing a
  real crash.
- `test_dlq_recovery_after_remap` — the "fix then replay" story: a renamed field
  is dead-lettered, then `recover_dlq` applies the remap and re-drives the rows.
- `test_bad_typed_record_dead_letters_not_crashes` and
  `test_recover_dlq_survives_unfixable_garbage` — a bad record goes to the DLQ
  instead of taking down the run, and unfixable garbage stays dead-lettered
  without crashing recovery.
- `test_validator_rejects_bad_records` — the validator rejects out-of-bounds /
  malformed / missing-field / bad-timestamp records (the physical-bounds and
  schema contract from `fie/config.py` and `docs/failure-model.md`).

### `test_eval_replay.py` — the evaluation & regression machinery (5 tests)
- `test_fixed_engine_scores_perfectly` / `test_buggy_engine_is_worse` — the eval
  harness ranks v1.2 above v1.1 (accuracy 100% vs 62%).
- `test_regression_says_ship_with_no_regressions` — candidate v1.2 vs baseline
  v1.1 yields fixes and zero regressions ⇒ SHIP.
- `test_reverse_regression_is_held` — swap the arguments and the *same* machinery
  returns HOLD (regressions). Proves the gate is symmetric and real.
- `test_replay_uses_snapshot_and_is_deterministic` — replay runs against the
  snapshotted `RunTrace.inputs`, so it's reproducible regardless of later store
  changes.

### `test_reliability.py` — the data-quality gate (3 tests)
Full coverage is not blocked; a telemetry gap drops the score below the 0.70 gate
and blocks; and the score is deterministic. These pin the thresholds in
`fie/config.py` (`GATE_MIN_SCORE`, `MAX_GAP_RATIO`, `STALE_SAMPLES`).

### `test_store.py` — persistence contracts (3 tests)
- `test_idempotency_contract` — `upsert_*` returns "inserted" / "duplicate" /
  "conflict" per the documented contract (same id + different payload ⇒ conflict,
  never a silent overwrite).
- `test_checkpoint_roundtrip` — set/get checkpoint survives a round trip (the
  basis of resumable ingest).
- `test_dlq_recover_cycle` — add → recover a dead letter end to end.

### `test_backends.py` — graceful degradation (3 tests)
- `test_grok_falls_back_without_key` / `test_claude_falls_back_without_key` — with
  no API key, the LLM backends transparently fall back to the rule engine. Proves
  the "never require a network" promise.
- `test_ml_engine_trains_scores_and_serves` — the optional ML engine trains,
  scores, and serves a prediction (skips/falls back cleanly if sklearn is absent).

## Mental model

> **Test the failure modes, not the happy path.** The valuable tests here don't
> check "does a clean feed ingest" — they check duplicates, crashes, renames,
> outages, and known bugs. Determinism + isolation are the enablers: because
> every test runs in a temp dir against a deterministic engine, "crash recovery"
> and "regression detected" become ordinary, fast, repeatable assertions.

## Interview Q&A

**Q: How many tests and what's the shape?** 26 test functions collecting to 41
cases — the difference is one test parametrized over the 16 golden scenarios. They
cover diagnosis correctness, ingestion robustness, the eval/regression harness,
the reliability gate, store contracts, and backend fallback.

**Q: How is the suite isolated?** `conftest.py` sets `FIE_DATA_DIR`, `FIE_DB`, and
`FIE_ENGINE=rule` to a temp dir at import time (before any config is read), and
every fixture builds on pytest's `tmp_path`. So tests never touch the real `data/`
and never make a network call — deterministic and hermetic by construction.

**Q: How do you test crash recovery without crashing?** `test_crash_midway_no_
double_count` rewinds the ingestion checkpoint to mid-file (simulating a process
that died before committing progress), re-drives ingest, and asserts the final row
count is unchanged. Idempotent upserts by deterministic id make every re-seen row
a harmless duplicate — that's exactly-once *effect* over at-least-once delivery.

**Q: Why test a known bug on purpose?** `test_v11_bug_calls_sensor_fault_a_
cooling_fault` documents and pins v1.1's misclassification. Without a
characterized baseline bug, the regression harness (v1.1 → v1.2, "fixed 6,
regressed 0, SHIP") would have nothing to prove against.

**Q: What does parametrize buy you here?** One test body, sixteen assertions —
each with the scenario key as its id — so the fixed engine's correctness is
verified per failure mode and a failure names the exact scenario. Cheaper and
clearer than sixteen near-duplicate functions.

**Q: What's the most important non-happy-path assertion?** Probably
`test_insufficient_data_is_gated_not_guessed`: on a telemetry outage the system
must abstain (`blocked`, `missing_evidence`, confidence < 0.3). In a plant, a
confident wrong RCA is worse than "I don't have enough data" — the test enforces
that ethic.

**Q: How does the suite stay offline?** `FIE_ENGINE=rule` forces the deterministic
engine, and `test_backends.py` proves the LLM backends fall back to it without a
key. No test ever depends on a network, so CI is reproducible.

## Resources

- pytest docs — fixtures, `tmp_path`, `parametrize`, `conftest.py`, markers
  (`docs.pytest.org`).
- "Python Testing with pytest" (Brian Okken) — the standard practical reference.
- pytest `ini_options` / configuration docs — matches `[tool.pytest.ini_options]`
  in `pyproject.toml`.
- In-repo: `tests/conftest.py`, `tests/test_agent.py`, `tests/test_ingestion.py`,
  `tests/test_eval_replay.py`, `tests/test_reliability.py`, `tests/test_store.py`,
  `tests/test_backends.py`.
</content>
