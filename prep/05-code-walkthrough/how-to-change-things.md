# How to Change Things — a cookbook

"I want to X" → the file(s) to touch, and what to re-run. Every change should end
with `make test` and, for behavior changes, `fie eval`.

## Add a new telemetry signal
1. `fie/config.py` → add to `SIGNAL_BOUNDS` and `NOMINAL`.
2. `fie/agent/features.py` → add to `SIGNALS` (feature vector grows; **retrain**
   the ML engine: `fie train`, or the model's feature contract check will reject
   the old artifact — that guard is intentional).
3. If it should drive a diagnosis: `fie/agent/engine.py:_classify` and
   `_key_signals_for`.
4. Re-run: `make demo && make test`.

## Add a new failure mode / incident type
1. `fie/simulator/scenarios.py` → write a `_yourmode(asset, key)` builder
   returning a `Scenario` (effects + maintenance + mes + labels), and add it to
   `_BUILDERS`.
2. Add a `root_cause_category` value in `fie/models.py` (`RootCauseCategory`) and
   a `RECOMMENDATIONS` entry in `fie/agent/engine.py`.
3. Teach `_classify` to detect it (and add its `_key_signals_for`).
4. `fie/eval` picks it up automatically (golden set is generated from scenarios).
5. Re-run: `python -m fie.cli eval` — expect the new case to pass.

## Change the reliability gate threshold or logic
- Threshold: `fie/config.py:GATE_MIN_SCORE` (and `MAX_GAP_RATIO`, `STALE_SAMPLES`).
- Scoring logic: `fie/reliability.py:assess`. The gate's blocked-report shape is
  in `fie/agent/reconstruct.py` (the `if rel.blocked:` branch).

## Tune the agent's reasoning (rule engine)
- All heuristics live in `fie/agent/engine.py:RuleBasedEngine._classify`.
  Thresholds (`TEMP_RISE`, `COOLANT_DROP_FRAC`, `LOAD_HIGH`, ...) are module
  constants at the top of `engine.py`.
- **Version it.** Bump to a new version string and keep the old one: this is what
  makes regression testing meaningful. Add to `ENGINES` if you want it selectable
  by name.

## Add or change a tool
- `fie/agent/tools.py:Toolbox`. A new tool should append a `ToolCall` so it shows
  up in traces and tool-usage eval.

## Add a new evaluation metric
1. `fie/eval/evaluators.py` → add a pure function `(trace|report, bundle,
   labels) -> score`.
2. `fie/eval/harness.py` → compute it per case, aggregate it, add to
   `CaseResult`/`EvalReport`, and (optionally) the pass criteria.

## Use Grok (or Claude) instead of the rule engine
- Export a key: `export XAI_API_KEY=...` (Grok) or `export ANTHROPIC_API_KEY=...`.
- Run with `--engine grok` (or `claude`, or leave `auto`). Model + endpoint:
  `fie/config.py:GROK_MODEL` / `GROK_BASE_URL` (or `FIE_GROK_MODEL` env).
- Transport lives in `fie/agent/llm.py:GrokEngine._complete`. If the model name
  is wrong or the key is missing, it falls back to the rule engine — check
  `data/runs/*.json` `engine` field to confirm which ran.

## Train / retrain the ML engine
- `fie generate-dataset --n-per-class 500` then `fie train --n-per-class 500`
  (or just `fie train`, which generates + trains). Artifact → `data/models/`.
- Serve it: `fie eval --engine ml`, `fie reconstruct-all --engine ml`.
- Features: `fie/agent/features.py`. Model/pipeline: `fie/ml/train.py`.

## Swap SQLite for Postgres
- `fie/schema.sql` is already Postgres-compatible DDL. The only SQLite-specific
  code is the connection layer in `fie/store.py:__init__` (and the
  `INSERT ... ON CONFLICT` in `set_checkpoint`, which Postgres also supports).
  Replace the `sqlite3.connect` with a psycopg connection and adjust parameter
  style (`?` → `%s`). Everything above the store is unchanged.

## Change the UI
- `fie/web/server.py` (routes + render functions) and
  `fie/web/templates/*.html`. It's plain Jinja + inline CSS, no build step.

## Add a CLI command
- `fie/cli.py`: write `cmd_x(args)`, add a subparser in `build_parser`, set
  `func=cmd_x`.

## Golden rule
After any change: `make test`. After any *behavior* change: `python -m fie.cli
eval` (and if you changed the engine, `python -m fie.cli regression` to prove no
regressions).
