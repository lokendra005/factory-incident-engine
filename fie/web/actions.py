"""Runnable pipeline actions for the Console page.

Each action reuses the exact CLI command functions (``fie/cli.py``) and captures
their console output, so the browser Console shows the same thing you'd see in a
terminal — no duplicated logic. Only a fixed whitelist of safe pipeline actions
is exposed; there is no arbitrary command execution.
"""
from __future__ import annotations

import contextlib
import io
import re
import traceback
from types import SimpleNamespace

from .. import cli

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# name -> (callable building+running the cli command, human label)
_ACTIONS = {
    "demo":            lambda p: cli.cmd_demo(SimpleNamespace()),
    "simulate":        lambda p: cli.cmd_simulate(SimpleNamespace(reset=True)),
    "ingest":          lambda p: cli.cmd_ingest(SimpleNamespace(no_resume=False)),
    "recover-dlq":     lambda p: cli.cmd_recover_dlq(SimpleNamespace()),
    "reconstruct-all": lambda p: cli.cmd_reconstruct_all(
                            SimpleNamespace(engine=p.get("engine") or None)),
    "eval":            lambda p: cli.cmd_eval(
                            SimpleNamespace(engine=p.get("engine") or "rule-based/1.2.0")),
    "regression":      lambda p: cli.cmd_regression(SimpleNamespace(
                            baseline=p.get("baseline", "rule-based/1.1.0"),
                            candidate=p.get("candidate", "rule-based/1.2.0"))),
    "train":           lambda p: cli.cmd_train(SimpleNamespace(
                            n_per_class=int(p.get("n_per_class", 150)), seed=13)),
    "status":          lambda p: cli.cmd_status(SimpleNamespace()),
}

ACTION_NAMES = list(_ACTIONS)


def run_action(name: str, params: dict | None = None) -> dict:
    """Run one whitelisted action; return {ok, log}. Never raises."""
    if name not in _ACTIONS:
        return {"ok": False, "log": f"unknown action: {name}"}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = _ACTIONS[name](params or {})
        log = _ANSI.sub("", buf.getvalue())
        # eval returns non-zero when a case fails; that's expected output, not a
        # crash — surface the log either way.
        return {"ok": code in (0, None), "log": log or "(no output)"}
    except Exception:
        return {"ok": False,
                "log": _ANSI.sub("", buf.getvalue()) + "\n" + traceback.format_exc()}
