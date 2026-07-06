# Python Patterns in the Factory Incident Engine

## What it is

FIE is a modern (Python ≥ 3.11) codebase that leans on a small, deliberate set of
patterns: **Pydantic v2** for validated domain models, **dataclasses** for
lightweight internal value objects, **type hints everywhere**, the **stdlib
`argparse`** CLI, the **stdlib `http.server`** for the web UI, **`pathlib`** for
all path handling, and **context managers** for resource/transaction safety. The
theme is "strict where data crosses a boundary, lightweight everywhere else, and
almost no third-party dependencies."

## Why it matters

An FDE ships integration code into environments they don't control — air-gapped
plants, locked-down VMs, someone else's Python. Every dependency is a liability
(a version conflict, a security review, a thing that isn't installed). FIE's
runtime deps are just **`pydantic` and `jinja2`** (`pyproject.toml`,
`requirements.txt`). That minimalism is a philosophy you should be able to defend:
it makes the tool trivial to deploy, reproducible in CI, and easy to reason about.
Knowing *why* each stdlib choice was made (and when you'd break the rule) is
exactly the judgment an interviewer is probing.

## Pydantic v2 — validation at the boundary

`fie/models.py` is the canonical layer. Raw plant data is messy and untyped; once
a record survives validation it becomes a strict model and "nothing downstream
ever sees the mess again" (the module docstring). Key patterns:

- **`BaseModel` subclasses** with typed fields: `TelemetryReading`,
  `MaintenanceRecord`, `MesEvent`, `EvidenceBundle`, `IncidentReport`, `RunTrace`.
- **`Literal` for closed enums** — `MaintenanceRecord.kind` is
  `Literal["inspection", "repair", "replace", "lubrication", "calibration"]`;
  `MesEvent.event` and `RootCauseCategory` are `Literal[...]`. Pydantic rejects
  any value outside the set at construction — the type *is* the validator. This is
  how the ingestion validator catches a bad `kind` without hand-written checks.
- **`Field(default_factory=list)`** for mutable defaults — the correct way to give
  each instance its own list (never `= []`, the classic Python footgun). Used all
  over `EvidenceBundle` and `IncidentReport`.
- **JSON round-tripping** via v2 methods: `model_dump_json()` to serialize and
  `model_validate_json()` to parse. `Store.save_incident` persists a report as
  `report.model_dump_json()`; `Store.get_incident` restores it with
  `IncidentReport.model_validate_json(...)` (`fie/store.py`). Traces do the same in
  `fie/agent/reconstruct.py` (`RunTrace.model_dump_json(indent=2)`), which is also
  why the run traces are git-friendly, human-readable JSON.
- **Methods on models** — models aren't anemic: `IncidentReport.cited_ids()`
  computes the set of evidence ids referenced anywhere in the report, and is used
  directly by the groundedness evaluator and the citation-resolves test.

Why Pydantic over dataclasses here: these types cross I/O boundaries (JSONL feed,
SQLite, HTTP, disk). You want *coercion + validation + serialization* for free.

## Dataclasses — internal value objects

Where data never crosses a boundary and you just want a cheap, immutable record,
FIE uses stdlib `dataclasses` instead of paying for Pydantic. `fie/simulator/
scenarios.py` defines `Effect`, `MaintenanceSpec`, `MesSpec`, and `Scenario` as
`@dataclass(frozen=True)`:

- **`frozen=True`** makes them immutable/hashable — the scenario catalog is
  ground truth and shouldn't be mutated after construction.
- **`field(default_factory=list)`** again for mutable defaults.
- **`__post_init__`** with `object.__setattr__` — because the dataclass is frozen,
  normal attribute assignment is blocked, so `Scenario.__post_init__` uses
  `object.__setattr__(self, "expected_category", self.expected_category or
  self.category)` to derive a default. This is the idiomatic way to compute a
  derived field on a frozen dataclass, and a nice detail to be able to explain.

The mental split: **Pydantic at the edges (untrusted/serialized data),
dataclasses in the core (trusted, in-process config).**

## Type hints — `from __future__ import annotations`

Almost every module opens with `from __future__ import annotations`. This makes
annotations lazy (PEP 563) so you can write `dict[str, tuple[float, float]]`,
`tuple[int, int] | None`, and forward references without runtime cost or import
gymnastics — even where it might matter for older interpreters. Combined with the
`>=3.11` floor, the code uses built-in generics (`list[...]`, `dict[...]`) and PEP
604 unions (`X | None`) throughout, e.g. `SIGNAL_BOUNDS: dict[str, tuple[float,
float]]` in `fie/config.py` and `gap_min: tuple[int, int] | None` in
`scenarios.py`. Type hints here are documentation *and* the Pydantic schema.

## The CLI — stdlib `argparse`

`fie/cli.py` builds a subcommand CLI with `argparse` and no third-party framework
(no Click/Typer). Patterns worth citing:

- **Subparsers**: `sub = p.add_subparsers(dest="cmd", required=True)`; each
  subcommand (`simulate`, `ingest`, `reconstruct`, `eval`, `regression`,
  `status`, `serve`, `demo`, `train`, …) gets its own parser and options.
- **`set_defaults(func=cmd_xxx)`** — the dispatch trick: each subparser stores its
  handler function, and `main()` just calls `args.func(args)`. Clean command
  routing without a big `if/elif` ladder.
- **Handlers return an int exit code**, and `main` returns it; `__main__` does
  `sys.exit(main())`. `cmd_eval` returns `1` when any case fails — that non-zero
  exit is what lets CI use `fie eval` as a build gate.
- **Lazy imports inside handlers** (`from .store import Store` *inside*
  `cmd_ingest`, etc.) so `fie --version` and `--help` stay instant and a broken
  optional backend never breaks the whole CLI.
- **`entry_points`**: `pyproject.toml` maps `fie = "fie.cli:main"` under
  `[project.scripts]`, so `pip install` gives you a real `fie` command.

## The web UI — stdlib `http.server`, no framework

`fie/web/server.py` serves the read-only UI on `http.server.ThreadingHTTPServer`
with a `BaseHTTPRequestHandler` subclass — deliberately *no* Flask/FastAPI. The
docstring states the reason: the whole engine should run with only pydantic +
jinja2 so `fie serve` works straight after `pip install -r requirements.txt` with
nothing else to provision. Patterns:

- A single `do_GET` that routes on `urlparse(self.path).path` to a handful of
  routes (`/`, `/regression`, `/incident/<id>`, `/healthz`, else 404).
- Jinja2 for templating (the one non-stdlib UI dep), with `select_autoescape` on
  to prevent HTML injection, and `_env.globals` for shared helpers.
- A blanket `try/except` that returns a 500 with a traceback, and a `finally` that
  always closes the per-request `Store`.
- `/healthz` returning `"ok"` — which the `docker-compose.yml` healthcheck hits.

When would you *not* do this? Anything with auth, many routes, or write
endpoints — then a framework earns its keep. For a read-only demo UI with four
routes, the stdlib is the right, dependency-free call. Being able to state that
trade-off is the point.

## `pathlib` — paths as objects

No string concatenation for paths anywhere. `fie/config.py` derives everything
from `ROOT = Path(__file__).resolve().parent.parent` and composes with the `/`
operator: `DATA_DIR / "raw"`, `DATA_DIR / "plant.db"`. `ensure_dirs()` loops and
calls `d.mkdir(parents=True, exist_ok=True)`. Elsewhere `Path.read_text()` /
`write_text()` (schema load in `store.py`, trace persistence in `reconstruct.py`),
and `Path.glob("*.jsonl")` for iterating raw files (`cli.py`). `pathlib` is
portable (Windows/Posix) and reads cleanly — a small but consistent signal of
modern Python.

Note also **environment-overridable config**: `DATA_DIR = Path(os.environ.get(
"FIE_DATA_DIR", ROOT / "data"))` and `DB_PATH`, `ENGINE`, model names all read
from env with sane defaults. This is what lets the test suite point everything at
a temp dir (see `tests/conftest.py`) and the Dockerfile set `FIE_DATA_DIR=/app/data`.

## Context managers — deterministic cleanup

- **`@contextmanager` for the DB transaction**: `Store.tx()` in `fie/store.py`
  yields a cursor, commits on success, and `rollback()`s on any exception before
  re-raising. Callers get atomic writes with `with store.tx() as cur:` and never
  leak a half-applied transaction.
- **`with` for file I/O** implicitly via `Path.read_text/write_text`, and for the
  DB connection lifecycle (`Store.close()`), the HTTP handler's `finally:
  store.close()`, and pytest fixtures that `yield` then close (`conftest.py`
  `store` fixture). The consistent pattern: acquire, use, guarantee release —
  even on error.

## Mental model

> **Types at the boundary, stdlib in the middle, config in the environment.**
> Untrusted data is forced through Pydantic the moment it enters; internal
> ground-truth is cheap frozen dataclasses; every subsystem is reachable with
> only the standard library plus two well-known packages; and anything a deployer
> might need to change (paths, DB, engine) is an env var with a default.

## Interview Q&A

**Q: Pydantic vs dataclass — when do you use which here?** Pydantic for anything
that crosses an I/O boundary and needs validation/serialization — the domain
models in `models.py` (JSONL in, SQLite/HTTP/disk out). Frozen dataclasses for
trusted in-process value objects that never get serialized from untrusted input —
the scenario catalog in `scenarios.py`. Pydantic costs more per object; you pay it
only where validation buys you something.

**Q: What does `from __future__ import annotations` buy you?** Lazy annotation
evaluation, so annotations are strings until inspected. You get modern
`list[str]`/`X | None` syntax, cheap forward references, and no import-time cost
for typing — while keeping compatibility. FIE uses it in essentially every module.

**Q: Why no web framework?** The UI is read-only with four routes; `http.server`
+ jinja2 covers it with zero extra dependencies, so `fie serve` runs anywhere
after a two-package install. I'd reach for FastAPI the moment there's auth, many
routes, request bodies, or async I/O — none of which this UI has.

**Q: How does the CLI dispatch commands?** Each subparser calls
`set_defaults(func=handler)`, and `main()` just does `args.func(args)`. Handlers
return int exit codes; `cmd_eval` returns non-zero on failure, which is what makes
`fie eval` usable as a CI gate. Imports are lazy inside handlers so `--help` and a
broken optional backend never slow down or break the CLI.

**Q: How is a mutable default handled correctly?** `Field(default_factory=list)`
in Pydantic and `field(default_factory=list)` in dataclasses — each instance gets
its own list, never the shared-mutable-default bug.

**Q: How do you set a derived field on a frozen dataclass?** In `__post_init__`,
use `object.__setattr__(self, name, value)`, since normal assignment is blocked by
`frozen=True`. `Scenario` does this to default `expected_category` to `category`.

**Q: How is the DB transaction made safe?** A `@contextmanager` method `Store.tx`
yields a cursor, commits on clean exit, and rolls back then re-raises on any
exception — so a failed multi-statement write leaves no partial state.

**Q: How is the code made deployable in a locked-down plant?** Two runtime deps
(pydantic, jinja2), stdlib for CLI/HTTP/DB (SQLite), pathlib for portability, and
all environment-specific settings (`FIE_DATA_DIR`, `FIE_DB`, `FIE_ENGINE`) as env
vars with defaults. It installs and runs with no external services.

## Resources

- Pydantic v2 docs — models, `Field`, `model_dump_json` / `model_validate_json`,
  migration from v1 (`docs.pydantic.dev`).
- Python `dataclasses` docs — `frozen`, `field(default_factory=...)`,
  `__post_init__`.
- Python `argparse` docs — subparsers and `set_defaults`.
- Python `http.server` docs — `BaseHTTPRequestHandler`, `ThreadingHTTPServer`
  (plus the note that it's for limited/dev use, which frames the trade-off).
- Python `pathlib` and `contextlib.contextmanager` docs.
- PEP 563 (postponed annotation evaluation), PEP 604 (`X | Y` unions), PEP 585
  (builtin generics).
- In-repo: `fie/models.py`, `fie/simulator/scenarios.py`, `fie/cli.py`,
  `fie/web/server.py`, `fie/config.py`, `fie/store.py`.
</content>
