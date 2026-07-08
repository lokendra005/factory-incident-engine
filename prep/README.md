# Preparation & Knowledge Base

Everything you need to **understand, defend, extend, and talk about** the
Factory Incident Engine — built for the Lunar (Forward Deployed Engineer)
conversation, but useful for any interview where this repo is the centerpiece.

The goal isn't to sound impressive. It's to be able to answer, calmly and
specifically, the four questions that separate "I built a demo" from "I can be
dropped into a factory":

> "Show me a failure." · "Show me the eval." · "Show me the metrics." ·
> "Why did you make this decision?"

## How to use this folder

1. **Skim [`00-roadmap.md`](00-roadmap.md)** — a 7-day plan mapping every topic
   to the exact files in the repo, with resources.
2. **Read [`05-code-walkthrough/`](05-code-walkthrough/) first if you're short on
   time.** It's the "what is where and how it flows" tour. If you can retell
   `data-flow-trace.md` from memory, you can whiteboard the whole system.
3. **Drill concepts** in `01-concepts/` and `02-data-engineering/` until you can
   explain each without notes.
4. **Rehearse** with `06-interview-prep/` — real questions, strong answers, and
   the honest weaknesses (say them before they're found).
5. **Know how to grow it** with `07-extending/` — including the honest answer to
   "can we train it on a large dataset?" (yes — the pipeline is built).

## Map of the folder

| Folder | What's inside |
|---|---|
| `00-roadmap.md` | Learning plan + resources + topic→file map |
| `01-concepts/` | Agents, LLM APIs, RAG, evaluation, groundedness, replay, observability |
| `02-data-engineering/` | Ingestion/ETL, idempotency, checkpoints, DLQ, schema drift, data quality, DBs |
| `03-manufacturing-domain/` | Plant systems (PLC/SCADA/MES/ERP/OPC-UA), failure physics, RCA |
| `04-python-and-tooling/` | pydantic, pytest, stdlib patterns, Docker, CI |
| `05-code-walkthrough/` | Architecture tour, data-flow trace, "how to change X" cookbook |
| `06-interview-prep/` | The four questions, likely Q&A, decision defense, honest weaknesses |
| `07-extending/` | Model backends (Grok/Claude/ML), training on large datasets, real-data scaling |

## Most-used pages (start here for an interview)
- [`06-interview-prep/opening-questions.md`](06-interview-prep/opening-questions.md) — what it does / why useful / what problem / what principle / how you got the idea, with a 60-second chained version.
- [`06-interview-prep/the-four-questions.md`](06-interview-prep/the-four-questions.md) — show a failure / the eval / the metrics / a decision.
- [`06-interview-prep/origin-story.md`](06-interview-prep/origin-story.md) — "why did this idea come to you?"
- [`05-code-walkthrough/architecture-and-results.md`](05-code-walkthrough/architecture-and-results.md) — one-page architecture + all the test/eval numbers.
- [`07-extending/training-and-datasets.md`](07-extending/training-and-datasets.md) & [`dataset-tracks-comparison.md`](07-extending/dataset-tracks-comparison.md) — the "train on a large dataset" answer + the 3 data tracks.

## The 90-second pitch (memorize this)

> "It reconstructs manufacturing incidents from messy plant telemetry — but the
> point isn't the diagnosis, it's everything that makes an agent safe to deploy
> around it. Fault-tolerant ingestion that dead-letters bad data instead of
> crashing, a data-quality gate that makes the agent abstain when the data can't
> be trusted, an evaluation harness with a labeled golden set, and deterministic
> replay so I can prove a change fixes a bug without introducing regressions.
> It ships with a real, documented bug — v1.1 blames every temperature rise on
> cooling — and the machinery that catches it: eval flags it, v1.2 fixes it, and
> replay shows 62% → 100% accuracy, 6 fixed, 0 regressed, SHIP. It runs offline
> in one command, and the same reconstruction contract is served by a rule
> engine, a trained classifier, or an LLM (Grok/Claude) interchangeably."
