# Docker & CI in the Factory Incident Engine

## What it is

Two layers of reproducibility wrap FIE:

1. **Docker** — a `Dockerfile` and a `docker-compose.yml` that build a single
   image which, on boot, populates the store and serves the web UI. `make docker`
   is the one-liner.
2. **GitHub Actions CI** — `.github/workflows/ci.yml` runs the test suite, an
   **evaluation gate**, an end-to-end demo, and a regression report on every push
   to `main` and every pull request, across Python 3.11 and 3.12.

Together they answer "does it run the same way on my laptop, in a container, and
in CI?" — and, crucially, "does a change that *silently* makes the AI worse fail
the build?"

## Why it matters

An FDE ships into environments they don't own and hands work off to teammates and
customers. "Works on my machine" is a liability. Containerization means the demo
you show a customer is the exact bytes that ran in CI. And the standout idea here —
**running the model-quality eval as a build gate** — is directly transferable to
any ML/agent product: correctness regressions should break the build the same way
a failing unit test does, because a plausible-but-wrong diagnosis is a bug even
though nothing throws an exception.

## Docker

### The image (`Dockerfile`)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 FIE_DATA_DIR=/app/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "python -m fie.cli demo && python -m fie.cli serve --host 0.0.0.0 --port 8000"]
```

Points to defend:

- **`python:3.12-slim`** — a small official base; slim drops build cruft to keep
  the image lean. FIE's tiny dependency set (pydantic + jinja2) means the image is
  small and fast to build.
- **Layer caching via copy order**: `requirements.txt` is copied and installed
  *before* the rest of the source. Docker caches layers, so as long as
  dependencies don't change, editing application code doesn't re-run `pip install`.
  This is the single most important Dockerfile optimization and worth naming.
- **`--no-cache-dir`** keeps the pip cache out of the image layer.
- **`ENV FIE_DATA_DIR=/app/data`** — reuses the same env-var config knob the tests
  use (`fie/config.py` reads `FIE_DATA_DIR`), so the container writes its store to
  a known path without code changes. `PYTHONUNBUFFERED=1` makes logs stream
  immediately rather than buffering.
- **`EXPOSE 8000`** documents the port; the actual publish happens in compose.
- **The `CMD`**: `fie demo` runs the whole pipeline (simulate → ingest → recover
  DLQ → reconstruct → eval → regression → persist) to populate the store, *then*
  `fie serve` launches the UI bound to `0.0.0.0` (all interfaces, required inside
  a container) on port 8000. So the container comes up with a fully populated,
  explorable dashboard — no manual setup.

### Orchestration (`docker-compose.yml`)

```yaml
services:
  factory-incident-engine:
    build: .
    image: factory-incident-engine
    ports: ["8000:8000"]
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
      interval: 10s
      timeout: 3s
      retries: 5
```

- **`ports: "8000:8000"`** maps the container port to the host so you can open the
  dashboard in a browser.
- **`healthcheck`** hits the `/healthz` route (served in `fie/web/server.py`,
  which returns `"ok"`) using only stdlib `urllib` — no need to install `curl` in
  the image. Compose marks the container healthy once it responds, and `make
  docker` (`docker compose up --build`) builds and runs it.

## CI (`.github/workflows/ci.yml`)

```yaml
on:
  push: { branches: [main] }
  pull_request:
jobs:
  test:
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - checkout / setup-python
      - pip install -r requirements.txt
      - name: Unit + integration tests       -> python -m pytest -q
      - name: Evaluation gate (fails ...)     -> python -m fie.cli eval
      - name: End-to-end demo smoke           -> python -m fie.cli demo
      - name: Regression report               -> python -m fie.cli regression
```

Four checks, run on a **matrix** of Python 3.11 and 3.12 (the code's supported
range from `pyproject.toml`'s `requires-python = ">=3.11"`), on push to `main` and
on every PR:

1. **Tests** — `pytest -q`, the 41-test suite. Standard correctness/regression
   for the code itself.
2. **Evaluation gate** — `fie eval`. This is the distinctive step. `cmd_eval`
   (`fie/cli.py`) scores the current engine against the golden set and **returns a
   non-zero exit code if any case fails** (`return 0 if not rep.failing() else
   1`). A non-zero exit fails the CI job. So if a change drops diagnosis accuracy —
   even with all unit tests green — **the build breaks**. Model quality is a build
   gate, not a dashboard someone reviews later.
3. **Demo smoke** — `fie demo` runs the full end-to-end pipeline; if any stage
   throws, CI catches it. Cheap integration coverage of the real command path a
   user runs.
4. **Regression report** — `fie regression` replays baseline v1.1 vs candidate
   v1.2 and prints the fixed/regressed verdict. In CI it documents the
   ship/hold decision in the logs.

## Why the eval gate is the headline

Traditional CI catches code that *crashes* or *fails an assertion*. An AI system
has a third failure mode: it runs fine, returns a confident answer, and is
**quietly wrong**. There's no exception to catch. FIE closes that gap by turning
the offline evaluation harness into an exit code: `fie eval` fails the build when
the engine misdiagnoses any golden scenario. That's the whole point of the golden
set (`data/golden/`, built from `fie/simulator/scenarios.py`) and the evaluators
(`fie/eval/evaluators.py`) — they make "did the AI get worse?" a first-class,
automatable, blocking question. This is the single most interview-worthy idea in
the tooling: **eval-as-a-gate**.

## Reproducibility — the through-line

Every design choice reinforces "same result everywhere":

- **Deterministic engine + fixed data**: the default rule engine is a pure
  function; the simulator and golden set are generated from a fixed catalog, so
  the eval verdict is identical on a laptop, in Docker, and in CI. (The tests even
  force `FIE_ENGINE=rule` and offline mode via `conftest.py`.)
- **No external services**: SQLite (stdlib), no network, no API keys required.
  The LLM backends fall back to the rule engine if a key is absent, so CI never
  flakes on an outage or a rate limit.
- **Pinned Python range + tiny deps**: `>=3.11`, only pydantic + jinja2 at
  runtime, tested on the exact versions CI runs.
- **Fixed timestamp horizon**: `TS_MIN_ISO`/`TS_MAX_ISO` in `fie/config.py` are
  constants (not wall-clock), so ingestion validation is reproducible in CI rather
  than depending on "now."
- **Snapshotted replay inputs**: `RunTrace.inputs` freezes what the engine saw, so
  regression comparisons are apples-to-apples over time.

## Mental model

> **CI should fail for every way the product can be wrong — including "correct
> code, worse answers."** Unit tests guard behavior; the eval gate guards
> *quality*; Docker guarantees the thing you demo is the thing you tested. The
> container's `demo && serve` startup means "clone, `make docker`, open a browser"
> reproduces the entire story with zero manual steps.

## Interview Q&A

**Q: Why copy `requirements.txt` before the rest of the source?** Docker layer
caching. Dependencies change rarely; source changes constantly. Installing deps in
an earlier layer means everyday code edits reuse the cached `pip install` layer
and rebuild in seconds instead of reinstalling everything.

**Q: What does the container do on startup?** The `CMD` runs `fie demo` to build
and populate the store end-to-end, then `fie serve --host 0.0.0.0 --port 8000`.
Binding `0.0.0.0` is required so the port is reachable from outside the container.
Result: a fully populated dashboard on first boot.

**Q: Why is there an eval step separate from the tests?** Because an AI can pass
every unit test and still get *worse* at its actual job. `fie eval` scores the
engine against the labeled golden set and exits non-zero on any miss (see
`cmd_eval` in `fie/cli.py`), so a quality regression fails the build exactly like
a broken test. It's eval-as-a-gate.

**Q: How does CI avoid flaking on the LLM backends?** It doesn't use them. The
suite and eval run on the deterministic rule engine; the LLM engines fall back to
it when no API key is present (proven by `test_backends.py`). No network in CI ⇒
no flakes.

**Q: Why test on a Python matrix?** `pyproject.toml` supports `>=3.11`, so CI runs
3.11 and 3.12 to catch version-specific breakage before a user hits it.

**Q: How does the healthcheck work without curl?** It runs a one-line Python
`urllib.request.urlopen` against the app's `/healthz` route (which returns "ok").
Using the interpreter that's already in the image avoids adding `curl` just for a
probe.

**Q: What makes the whole thing reproducible?** A pure deterministic engine, a
fixed generated dataset, fixed timestamp constants, SQLite with no external
services, snapshotted replay inputs, and pinned Python/deps. The same command
yields the same verdict on any machine — which is exactly what lets the eval gate
be trustworthy.

## Resources

- Docker docs — Dockerfile best practices (layer caching, `--no-cache-dir`, slim
  base images), `docker compose`, and `HEALTHCHECK` (`docs.docker.com`).
- GitHub Actions docs — workflow syntax, `strategy.matrix`, `actions/checkout`,
  `actions/setup-python` (`docs.github.com/actions`).
- "Continuous Delivery" (Humble & Farley) — the build-gate philosophy.
- ML-eval-as-CI references — e.g. writing about "evals" / "LLM evaluation
  harnesses" as regression gates (OpenAI Evals, and general MLOps CI/CD guidance).
- In-repo: `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`,
  `Makefile` (`docker`, `test`, `eval` targets), `fie/cli.py` (`cmd_eval` exit
  code), `docs/evaluation.md`.
</content>
