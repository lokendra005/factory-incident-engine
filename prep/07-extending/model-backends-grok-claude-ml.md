# Model Backends: Grok, Claude, and ML

The project has one reconstruction *contract* and several *engines* that satisfy
it. This doc explains each and how to switch between them.

## The contract

Every engine implements:

```python
reconstruct(bundle: EvidenceBundle, reliability: float) -> (IncidentReport, [ToolCall])
```

Because of this, `fie eval`, `fie regression`, the UI, and the CLI don't care
which engine ran. Selection is one flag: `--engine {rule|rule-1.1|grok|claude|ml|auto}`.

`auto` resolution (`agent/engine.py:get_engine`): explicit `FIE_ENGINE` wins →
else Grok if `XAI_API_KEY` present → else Claude if `ANTHROPIC_API_KEY` present →
else rule.

## Grok (xAI) — your free-key path

- **Wired in** `agent/llm.py:GrokEngine`. Uses xAI's **OpenAI-compatible** REST
  endpoint (`POST {GROK_BASE_URL}/chat/completions`) via `httpx` — no SDK needed.
- **Setup:**
  ```bash
  export XAI_API_KEY=xai-...            # from https://console.x.ai
  export FIE_GROK_MODEL=grok-2-latest   # check the console for current names
  fie reconstruct-all --engine grok
  ```
- **How it works:** builds a compact evidence summary
  (`LLMEngine._summarize`), sends it with a strict system prompt asking for a
  **JSON object** (`response_format: {"type":"json_object"}`, `temperature: 0`),
  parses the JSON, and **drops any cited id not in the bundle** (grounding guard).
- **Fallback:** any error (bad key, wrong model name, network, bad JSON) →
  deterministic rule engine, with `report.engine` noting it fell back. So the
  demo/eval/CI never depend on it.
- **Verify which ran:** open `data/runs/RUN-*.json` and read the `engine` field,
  or the incident page in the UI.

Common gotchas: (1) wrong model name → 404 → silent fallback (check the console);
(2) free-tier rate limits → intermittent fallback (expected, harmless);
(3) the summary caps tokens on purpose — don't dump raw telemetry at the model.

## Claude — the other LLM path

- `agent/llm.py:ClaudeEngine`, via the `anthropic` SDK (optional dependency),
  default model `config.CLAUDE_MODEL` (`claude-opus-4-8`).
- `export ANTHROPIC_API_KEY=...; pip install anthropic; fie ... --engine claude`.
- Same summary + JSON + grounding contract as Grok; same fallback behavior.

## ML classifier — the trained path

- `agent/ml_engine.py:MLEngine`, backed by `ml/train.py` (sklearn
  `StandardScaler → RandomForest`).
- Train first (`fie train`), then `--engine ml`. Predicts category + probability
  (→ confidence); reuses the shared timeline/grounding scaffolding.
- Feature parity with training is enforced: the artifact stores `feature_names`
  and `MLEngine.__init__` raises on mismatch (→ `get_engine` falls back to rule).
- See `07-extending/training-and-datasets.md`.

## Rule engine — the default

- `agent/engine.py:RuleBasedEngine`, deterministic. Two versions (`1.1.0` buggy,
  `1.2.0` fixed) exist to make regression testing real. Default is `1.2.0`.

## Why keep all of them?

- **Rule** = debuggable, offline, deterministic, zero-cost — the right default
  for six well-understood signals.
- **LLM** = flexible reasoning + natural-language explanation when signals are
  novel or the operator wants a narrative; but non-deterministic and costs
  tokens.
- **ML** = shines when the signal space is large/noisy and thresholds get
  brittle.

The point of the abstraction: **choosing a backend is a deployment decision, not
a rewrite**, and whichever you choose is scored by the same eval and gated by the
same replay/regression check.

## Adding a fourth backend
Implement the contract, add a branch in `get_engine`, and it's immediately
eval-able and replay-able. That extensibility is the deliverable.
