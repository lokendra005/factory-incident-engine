"""Command-line interface.

    fie demo            end-to-end story in one command (what `make demo` runs)
    fie simulate        write the messy raw feed + build the golden set
    fie ingest          ingest raw feed -> store (crash-safe, resumable)
    fie recover-dlq     re-drive dead-lettered records after a fix
    fie reconstruct-all reconstruct every incident window from the store
    fie reconstruct     reconstruct one asset/window
    fie eval            score an engine against the golden set
    fie regression      side-by-side: baseline engine vs candidate engine
    fie status          store + data-quality snapshot
    fie serve           launch the web UI
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import AGENT_VERSION, config


def _hr(title: str) -> None:
    print(f"\n\033[1m{'─' * 3} {title} {'─' * (56 - len(title))}\033[0m")


def _load_manifest() -> dict:
    p = Path(config.RAW_DIR) / "manifest.json"
    if not p.exists():
        return {"windows": []}
    return json.loads(p.read_text())


def cmd_simulate(args) -> int:
    from .simulator import SCENARIOS, write_raw_feed
    from .eval.golden import build_golden
    config.ensure_dirs()
    if args.reset:
        from .store import Store
        s = Store(); s.reset(); s.close()
        cp = Path(config.RAW_DIR)
        for f in cp.glob("*.jsonl"):
            f.unlink()
    manifest = write_raw_feed(SCENARIOS)
    n_golden = build_golden()
    _hr("SIMULATE")
    print(f"raw telemetry lines : {manifest['telemetry_lines']}")
    print(f"injected defects    : {manifest['injected']}")
    print(f"incident windows    : {len(manifest['windows'])}")
    print(f"golden cases        : {n_golden}")
    return 0


def cmd_ingest(args) -> int:
    from .store import Store
    from .ingestion import ingest_all
    s = Store()
    out = ingest_all(s, resume=not args.no_resume)
    _hr("INGEST")
    for name, st in out.items():
        print(f"{name}: read={st['read']} inserted={st['inserted']} "
              f"dup={st['duplicate']} out_of_order={st['out_of_order']} "
              f"dlq={st['dlq_total']} drift={st['drift']}")
        if st["dlq"]:
            print(f"    dlq reasons: {st['dlq']}")
    print(f"store counts: {s.counts()}")
    s.close()
    return 0


def cmd_recover_dlq(args) -> int:
    from .store import Store
    from .ingestion import recover_dlq
    s = Store()
    res = recover_dlq(s)
    _hr("RECOVER DLQ")
    print(f"recovered={res['recovered']} still_dead_lettered={res['still_dead_lettered']}")
    print(f"store counts: {s.counts()}")
    s.close()
    return 0


def cmd_reconstruct_all(args) -> int:
    from .store import Store
    from .agent.engine import get_engine
    from .agent.reconstruct import reconstruct_from_store
    s = Store()
    engine = get_engine(args.engine)
    manifest = _load_manifest()
    _hr(f"RECONSTRUCT ALL  (engine={engine.name})")
    for w in manifest["windows"]:
        tr = reconstruct_from_store(s, w["asset"], w["window_start"],
                                    w["window_end"], engine=engine)
        flag = " [BLOCKED]" if tr.report.blocked else ""
        print(f"  {tr.incident_id} {w['asset']:9} -> "
              f"{tr.report.root_cause_category:20} "
              f"conf={tr.report.confidence:.2f}{flag}")
    s.close()
    return 0


def cmd_reconstruct(args) -> int:
    from .store import Store
    from .agent.engine import get_engine
    from .agent.reconstruct import reconstruct_from_store
    s = Store()
    tr = reconstruct_from_store(s, args.asset, args.start, args.end,
                                engine=get_engine(args.engine))
    print(tr.report.model_dump_json(indent=2))
    s.close()
    return 0


def cmd_eval(args) -> int:
    from .eval import evaluate, build_golden
    build_golden()
    rep = evaluate(args.engine)
    _hr("EVALUATION")
    print(rep.summary_line())
    for c in rep.cases:
        mark = "\033[32m✓\033[0m" if c.passed else "\033[31m✗\033[0m"
        print(f"  {mark} {c.key:28} exp={c.expected_category:20} "
              f"got={c.got_category:20} ground={c.groundedness:.2f}")
    if rep.failing():
        print(f"\n{len(rep.failing())} case(s) failed.")
    return 0 if not rep.failing() else 1


def cmd_regression(args) -> int:
    from .eval import build_golden
    from .replay import run_regression
    build_golden()
    rep = run_regression(args.baseline, args.candidate)
    _hr("REGRESSION")
    print(rep.summary_line())
    for r in rep.rows:
        if r.status in ("fixed", "regressed", "changed"):
            tag = {"fixed": "\033[32mFIXED\033[0m", "regressed": "\033[31mREGRESSED\033[0m",
                   "changed": "changed"}[r.status]
            print(f"  {tag:20} {r.asset:9} {r.old_category} -> {r.new_category} "
                  f"(expected {r.expected})")
    return 0


def cmd_status(args) -> int:
    from .store import Store
    from .reliability import assess
    from .models import EvidenceBundle
    s = Store()
    _hr("STORE")
    print(f"counts: {s.counts()}")
    print(f"dlq (unrecovered): {s.dlq_counts()}")
    drift = s.drift_items()
    if drift:
        print(f"schema drift: {[(d['field'], d['kind']) for d in drift]}")
    _hr("RELIABILITY GATE (per incident window)")
    manifest = _load_manifest()
    for w in manifest["windows"][:20]:
        bundle = EvidenceBundle(
            asset=w["asset"], window_start=w["window_start"], window_end=w["window_end"],
            readings=s.query_readings(w["asset"], w["window_start"], w["window_end"]),
        )
        rel = assess(bundle)
        gate = "\033[31mBLOCKED\033[0m" if rel.blocked else "\033[32mOK\033[0m"
        print(f"  {w['asset']:9} {w['window_start'][:16]}  "
              f"reliability={rel.overall:.0%}  {gate}")
    s.close()
    return 0


def cmd_generate_dataset(args) -> int:
    from .ml import generate_dataset
    _hr("GENERATE DATASET")
    X, y, rows = generate_dataset(n_per_class=args.n_per_class, seed=args.seed)
    from collections import Counter
    print(f"generated {len(rows)} labeled samples "
          f"({args.n_per_class}/class) -> {config.DATASET_DIR}/train.jsonl")
    print(f"class balance: {dict(Counter(y))}")
    return 0


def cmd_train(args) -> int:
    from .ml import train_model, train_external
    _hr("TRAIN ML ENGINE")
    if args.source == "synthetic":
        res = train_model(n_per_class=args.n_per_class, seed=args.seed)
        print(f"model {res['version']}: trained on {res['n_train']} samples, "
              f"held-out accuracy {res['val_accuracy']:.1%}")
        print(f"saved -> {res['path']}")
        print("now usable as:  fie eval --engine ml   |   fie reconstruct-all --engine ml")
        return 0

    # external real dataset (AI4I 2020 CSV, or Azure PdM directory)
    if args.source == "azure_pdm":
        if not args.data_dir:
            print("--data-dir PATH (folder with the 5 PdM_*.csv files) is required "
                  "for --source azure_pdm")
            return 1
        path = args.data_dir
    else:
        if not args.csv:
            print(f"--csv PATH is required for --source {args.source}")
            return 1
        path = args.csv
    res = train_external(args.source, path, seed=args.seed,
                         failures_only=args.failures_only)
    print(f"[{args.source}] {res['n_samples']} samples, {res['n_features']} features, "
          f"{len(res['classes'])} classes: {res['classes']}")
    print(f"held-out accuracy {res['val_accuracy']:.1%}  (see per-class report below)")
    print(f"saved -> {res['path']}\n")
    print(res["report"])
    print("NOTE: this is a separate real-dataset track. It demonstrates the "
          "training pipeline on real data; it is NOT served by the incident "
          "reconstruction engine (different feature/label space).")
    return 0


def cmd_serve(args) -> int:
    from .web.server import serve
    serve(host=args.host, port=args.port)
    return 0


def cmd_demo(args) -> int:
    """The full loop, in one command."""
    from .store import Store
    from .simulator import SCENARIOS, write_raw_feed
    from .eval import build_golden, evaluate
    from .ingestion import ingest_all, recover_dlq
    from .agent.engine import get_engine
    from .agent.reconstruct import reconstruct_from_store
    from .replay import run_regression

    config.ensure_dirs()
    s = Store(); s.reset()
    for f in Path(config.RAW_DIR).glob("*.jsonl"):
        f.unlink()

    _hr("1. SIMULATE  (messy plant feed)")
    manifest = write_raw_feed(SCENARIOS)
    build_golden()
    print(f"telemetry lines={manifest['telemetry_lines']} "
          f"injected={manifest['injected']}")

    _hr("2. INGEST  (validate / dedupe / DLQ / drift / checkpoint)")
    out = ingest_all(s)
    tel = out["telemetry.jsonl"]
    print(f"inserted={tel['inserted']} deduped={tel['duplicate']} "
          f"dead-lettered={tel['dlq_total']} {tel['dlq']}")

    _hr("3. RECOVER DLQ  (fix upstream rename -> replay dead letters)")
    print(recover_dlq(s))

    _hr("4. RECONSTRUCT  (buggy engine v1.1.0)")
    eng11 = get_engine("rule-based/1.1.0")
    for w in manifest["windows"]:
        tr = reconstruct_from_store(s, w["asset"], w["window_start"], w["window_end"], engine=eng11)
        bad = "" if tr.report.root_cause_category == w["expected_category"] else "  <-- WRONG"
        if bad or tr.report.blocked:
            print(f"  {w['asset']:9} got={tr.report.root_cause_category:20} "
                  f"expected={w['expected_category']}{bad}")

    _hr("5. EVALUATE  v1.1.0 vs v1.2.0")
    print("  " + evaluate("rule-based/1.1.0").summary_line())
    print("  " + evaluate("rule-based/1.2.0").summary_line())

    _hr("6. REPLAY + REGRESSION  (candidate v1.2.0 on captured traces)")
    reg = run_regression("rule-based/1.1.0", "rule-based/1.2.0")
    print("  " + reg.summary_line())

    # leave the store populated with the GOOD engine for the UI
    _hr("7. FINALIZE  (persist v1.2.0 incidents for the UI)")
    eng12 = get_engine("rule-based/1.2.0")
    for w in manifest["windows"]:
        reconstruct_from_store(s, w["asset"], w["window_start"], w["window_end"], engine=eng12)
    print(f"  persisted {len(manifest['windows'])} incidents. Run `fie serve` to explore.")
    s.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fie", description="Factory Incident Engine")
    p.add_argument("--version", action="version", version=f"fie {AGENT_VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("simulate"); s.add_argument("--reset", action="store_true")
    s.set_defaults(func=cmd_simulate)

    s = sub.add_parser("ingest"); s.add_argument("--no-resume", action="store_true")
    s.set_defaults(func=cmd_ingest)

    sub.add_parser("recover-dlq").set_defaults(func=cmd_recover_dlq)

    s = sub.add_parser("reconstruct-all"); s.add_argument("--engine", default=None)
    s.set_defaults(func=cmd_reconstruct_all)

    s = sub.add_parser("reconstruct")
    s.add_argument("--asset", required=True); s.add_argument("--start", required=True)
    s.add_argument("--end", required=True); s.add_argument("--engine", default=None)
    s.set_defaults(func=cmd_reconstruct)

    s = sub.add_parser("eval"); s.add_argument("--engine", default="rule-based/1.2.0")
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("regression")
    s.add_argument("--baseline", default="rule-based/1.1.0")
    s.add_argument("--candidate", default="rule-based/1.2.0")
    s.set_defaults(func=cmd_regression)

    sub.add_parser("status").set_defaults(func=cmd_status)

    s = sub.add_parser("generate-dataset")
    s.add_argument("--n-per-class", type=int, default=300); s.add_argument("--seed", type=int, default=13)
    s.set_defaults(func=cmd_generate_dataset)

    s = sub.add_parser("train")
    s.add_argument("--source", choices=["synthetic", "ai4i", "azure_pdm"],
                   default="synthetic",
                   help="synthetic generator (default) or a real dataset loader")
    s.add_argument("--csv", default=None, help="path to the dataset CSV (for --source ai4i)")
    s.add_argument("--data-dir", default=None,
                   help="folder with the 5 PdM_*.csv files (for --source azure_pdm)")
    s.add_argument("--failures-only", action="store_true",
                   help="ai4i: train only on failure rows (mode identification)")
    s.add_argument("--n-per-class", type=int, default=300)
    s.add_argument("--seed", type=int, default=13)
    s.set_defaults(func=cmd_train)

    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    sub.add_parser("demo").set_defaults(func=cmd_demo)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
