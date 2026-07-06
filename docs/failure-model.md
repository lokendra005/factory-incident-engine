# Failure model

Two kinds of failure are modeled on purpose: **data failures** (what ingestion
must survive) and **diagnostic failures** (what evaluation must catch).

## Data failures — injected into the raw feed

`fie/simulator/generate.py` corrupts the telemetry stream so ingestion has
something real to defend against. Each has a specific handling contract:

| Injected defect | Ingestion contract | DLQ reason |
|---|---|---|
| Exact duplicate line | deduped via deterministic id | *(not dead-lettered)* |
| Out-of-order arrival | stored anyway (ts-based); counted | *(handled)* |
| Impossible value (out of physical bounds) | rejected | `out_of_bounds` |
| Malformed JSON | rejected | `malformed_json` |
| Missing required field | rejected | `missing_field:*` |
| Future / clock-skew timestamp | rejected | `future_timestamp` |
| Naive timestamp (no tz) | rejected | `ts_naive_no_timezone` |
| New field appears mid-stream (`unit`) | tolerated + logged | drift `new_field` |
| Field renamed (`value`→`reading_c`) | dead-lettered, then **recoverable** | `schema_missing_value` |

The renamed-field batch is the "fix, then replay the DLQ" story:
`recover_dlq()` applies a `reading_c → value` remap, re-validates, and re-drives
the recovered rows idempotently.

Crash safety is proven by test: rewind the checkpoint mid-file, re-drive, and the
row count is unchanged (every re-seen row is an idempotent duplicate).

## Diagnostic failures — the eight scenarios

Chosen so the *easy-to-confuse pairs* are the whole point:

| Category | Signature | Confusable with |
|---|---|---|
| `cooling_degradation` | coolant flow ↓ **and** temp ↑ (correlated) | sensor_fault, overload |
| `sensor_fault` | temp ↑ (often a step) with coolant/load **nominal** | cooling_degradation |
| `overload` | load pinned ~100% drives temp ↑; coolant nominal | cooling_degradation |
| `bearing_wear` | vibration ↑ → fault | operator_config |
| `tool_wear` | defect rate ↑, gradual | operator_config |
| `operator_config` | degradation begins right **after** a config change | tool_wear |
| `no_incident` | all signals nominal | *(must not invent a cause)* |
| `unknown` | telemetry outage → gate blocks | *(must abstain)* |

## The documented bug: v1.1.0

`rule-based/1.1.0` uses a temperature-first heuristic:

```python
if temp_rise:
    return "cooling_degradation"   # BUG: never checks whether coolant actually dropped
```

So it misclassifies **sensor faults** and **overloads** as cooling degradation,
and (because a step change perturbs its baseline window) mislabels the
**operator-config** cases as tool wear. Six of sixteen wrong — and every wrong
answer is *fully grounded*: it cites real temperature readings. That's what makes
it a realistic bug rather than an obvious one.

## The fix: v1.2.0

Requires corroboration before a thermal diagnosis:

```python
if load_high and temp_rise:            return "overload"
if coolant_drop and temp_rise:         return "cooling_degradation"
if temp_rise and not coolant_drop and not load_high:
                                       return "sensor_fault"   # temp rose with no driver
```

`fie regression --baseline rule-based/1.1.0 --candidate rule-based/1.2.0`:

```
accuracy 62% → 100%  |  fixed 6, regressed 0  ⇒ SHIP
```

Reverse the arguments and the same machinery returns `HOLD` (6 regressions). The
verdict — not the diagnosis — is the deliverable.
