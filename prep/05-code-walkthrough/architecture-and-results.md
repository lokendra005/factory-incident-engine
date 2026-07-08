# Architecture & Results (one-page summary)

A single page you can review right before an interview: how it works, then the
concrete numbers. Deeper versions live in `architecture-walkthrough.md` and
`data-flow-trace.md`; the eval details are in `../01-concepts/evaluation-harness.md`.

## The shaping principle
The reasoning engine is a **pure function of its evidence** (`EvidenceBundle ->
IncidentReport`). That purity is why evaluation is reproducible and replay is
deterministic. Two operating rules follow from it:
- **Nothing is trusted that shouldn't be** — bad data is dead-lettered, not
  crashed on; low-quality windows make the agent abstain.
- **Nothing ships that can't be proven safe** — every run is a replayable trace,
  so a change is judged against captured reality before release.

## The pipeline

```
 simulate → INGEST → normalized store → reliability GATE → reconstruct → EVALUATE
 (messy feed)  │  validate/dedupe/DLQ/drift/checkpoint          │  block if       │  vs golden set
               │                                                │  data untrusted ▼
               ▼                                          (abstain, don't guess)  a WRONG answer?
          recover DLQ                                                              │
          (fix → replay)                                                          ▼
                                                                          REPLAY captured trace
                                                                          vs new engine → REGRESSION
                                                                          fixed? regressed? → SHIP/HOLD
```

| Layer | Module | Role |
|---|---|---|
| Simulator | `fie/simulator/` | 8 failure modes + injected data mess (dupes, out-of-order, impossible, malformed, future ts, drift) |
| Ingestion | `fie/ingestion/` | validate → dedupe → DLQ → drift → checkpoint; exactly-once; `recover_dlq` |
| Store | `fie/store.py` | normalized SQLite (Postgres-compatible), idempotent upserts |
| Gate | `fie/reliability.py` | data-quality score; **blocks** the agent on untrustworthy windows |
| Agent | `fie/agent/` | toolbox + grounded report; 3 backends: rule / ML / LLM (Grok, Claude) |
| Eval | `fie/eval/` | golden set + correctness / groundedness / timeline / tool-usage / abstention |
| Replay | `fie/replay/` | deterministic replay of captured traces + regression → SHIP/HOLD |
| UI / CLI | `fie/web/`, `fie/cli.py` | control room (live engine switch, charts, Console) + commands |

## Results

**Tests:** 49 passing. **CI green on Python 3.11 & 3.12** (pytest + eval gate +
demo + regression).

**Ingestion (demo run):**
| metric | value |
|---|---|
| raw telemetry lines | 4,807 |
| inserted | 4,560 |
| deduped | 98 |
| dead-lettered | 149 (`out_of_bounds 49, malformed_json 31, missing_field 31, future_timestamp 25, schema_missing_value 13`) |
| recovered after remap | 13 |
| crash test | checkpoint rewound → re-ingest inserted **0 new**, count unchanged (exactly-once) |

**Engine evaluation (16-case golden set):**
| engine | accuracy | groundedness | pass |
|---|---|---|---|
| rule-based/1.2.0 (fixed) | **100%** | 1.00 | 100% |
| rule-based/1.1.0 (bug) | 62% | 1.00 | 62% |

Groundedness is 1.00 even for the buggy engine — it cites real readings but
reasons wrong. That's what a realistic production bug looks like.

**Replay / regression:**
- `rule-1.1 → rule-1.2`: **62% → 100%, 6 fixed, 0 regressed → SHIP**
- reverse: **HOLD, 6 regressions**

**ML training tracks:**
| track | data | result |
|---|---|---|
| Synthetic (served by UI) | 4,000 | 100% held-out (*in-distribution* — proves plumbing + train/serve parity) |
| AI4I 2020 (full) | 10,000 real | 99.1% acc but **macro-F1 0.56** (imbalance exhibit) |
| AI4I 2020 (failures-only) | 357 | 93.1% acc, macro-F1 0.76 |
| Azure PdM (real, multi-source) | 876,100 → 2,283 windows | **95.4% acc, 0.90 macro-F1** (comp1–4 F1 0.82–0.92) |

**Backends:** Grok falls back to rule cleanly without a key (verified). ML engine
serves through the same eval harness and enforces feature-contract parity.

**UI:** 20/20 requirement probes passed against the live server.

## The headline
> "The buggy engine passed 62%; evaluation caught the failures; the fix took it to
> 100%, and replay proved it — 6 fixed, 0 regressed, ship. And I validated the
> training pipeline on the real Microsoft Azure PdM benchmark at 0.90 macro-F1."
