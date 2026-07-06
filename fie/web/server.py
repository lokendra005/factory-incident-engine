"""Interactive control-room UI on the stdlib http.server.

Still zero-framework (only pydantic + jinja2), but now it *drives* the machinery
instead of only displaying it:
  * Incident page re-runs reconstruction live with any engine you pick
    (watch rule-based/1.1.0 get the sensor-fault wrong, then 1.2.0 fix it), and
    renders server-side SVG telemetry charts so the failure is visible.
  * Regression page runs a chosen baseline-vs-candidate diff on demand.
  * The dashboard can recover the dead-letter queue live.

Interactivity is form/GET-driven (server-rendered), so it needs no JavaScript
and screenshots reliably.
"""
from __future__ import annotations

import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pathlib import Path

from .. import __version__, config
from ..agent.engine import get_engine
from ..agent.reconstruct import reconstruct_from_store
from ..ingestion import recover_dlq
from ..models import EvidenceBundle
from ..reliability import assess
from ..store import Store
from . import charts

_TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATES)),
                   autoescape=select_autoescape(["html"]))

CATEGORY_COLOR = {
    "cooling_degradation": "#4fc3f7", "sensor_fault": "#ce93d8",
    "bearing_wear": "#ffb74d", "tool_wear": "#fff176", "overload": "#ff8a65",
    "operator_config": "#81c784", "no_incident": "#90a4ae", "unknown": "#e57373",
}
ENGINE_OPTIONS = ["rule-based/1.2.0", "rule-based/1.1.0", "ml", "grok", "claude"]
CHART_SIGNALS = ["spindle_temp_c", "coolant_flow_lpm", "vibration_mm_s",
                 "spindle_load_pct", "defect_rate_pct"]

_env.globals["cat_color"] = lambda c: CATEGORY_COLOR.get(c, "#90a4ae")
_env.globals["version"] = __version__
_env.globals["engine_options"] = ENGINE_OPTIONS

PIPELINE = [
    ("Simulate", "messy feed"), ("Ingest", "dedupe · DLQ · drift"),
    ("Store", "normalized"), ("Gate", "trust check"),
    ("Reconstruct", "agent"), ("Evaluate", "golden set"), ("Replay", "SHIP/HOLD"),
]


def _reliability_for(store, asset, start, end):
    bundle = EvidenceBundle(
        asset=asset, window_start=start, window_end=end,
        readings=store.query_readings(asset, start, end),
        maintenance=store.query_maintenance(asset, start, end),
        mes=store.query_mes(asset, start, end))
    return assess(bundle)


def render_dashboard(store) -> str:
    incidents = store.list_incidents()
    rel_rows, seen = [], set()
    for r in incidents:
        key = (r.asset, r.window_start)
        if key in seen:
            continue
        seen.add(key)
        rel_rows.append({"asset": r.asset, "start": r.window_start[:16],
                         "rel": _reliability_for(store, r.asset, r.window_start, r.window_end)})
    return _env.get_template("dashboard.html").render(
        counts=store.counts(), dlq=store.dlq_counts(),
        drift=[dict(d) for d in store.drift_items()],
        incidents=incidents, rel_rows=rel_rows, pipeline=PIPELINE)


def render_incident(store, incident_id: str, engine_name: str | None) -> str:
    stored = store.get_incident(incident_id)
    if stored is None:
        return _env.get_template("notfound.html").render(what=incident_id)

    # live re-run with the chosen engine (does not overwrite the stored incident)
    if engine_name and engine_name in ENGINE_OPTIONS:
        trace = reconstruct_from_store(store, stored.asset, stored.window_start,
                                       stored.window_end,
                                       engine=get_engine(engine_name), persist=False)
        report = trace.report
        selected = engine_name
    else:
        report = stored
        selected = stored.engine

    rel = _reliability_for(store, report.asset, report.window_start, report.window_end)
    readings = store.query_readings(report.asset, report.window_start, report.window_end)
    mes = store.query_mes(report.asset, report.window_start, report.window_end)
    charts_html = charts.incident_charts(CHART_SIGNALS, readings, mes)
    return _env.get_template("incident.html").render(
        r=report, rel=rel, charts_html=charts_html, selected_engine=selected)


def render_regression(store, baseline: str, candidate: str) -> str:
    from ..eval import build_golden
    from ..replay import run_regression
    build_golden()
    rep = run_regression(baseline, candidate)
    return _env.get_template("regression.html").render(
        rep=rep, baseline=baseline, candidate=candidate)


class Handler(BaseHTTPRequestHandler):
    server_version = f"FIE/{__version__}"

    def log_message(self, *a):
        pass

    def _send(self, body: str, status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to: str):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        q = parse_qs(parsed.query)
        store = Store()
        try:
            if path == "/":
                self._send(render_dashboard(store))
            elif path == "/regression":
                self._send(render_regression(
                    store, q.get("baseline", ["rule-based/1.1.0"])[0],
                    q.get("candidate", ["rule-based/1.2.0"])[0]))
            elif path.startswith("/incident/"):
                self._send(render_incident(store, path.split("/incident/", 1)[1],
                                           q.get("engine", [None])[0]))
            elif path == "/healthz":
                self._send("ok")
            else:
                self._send(_env.get_template("notfound.html").render(what=path), 404)
        except Exception:
            self._send(f"<pre>{traceback.format_exc()}</pre>", 500)
        finally:
            store.close()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        store = Store()
        try:
            if path == "/actions/recover-dlq":
                recover_dlq(store)
                self._redirect("/")
            else:
                self._send(_env.get_template("notfound.html").render(what=path), 404)
        except Exception:
            self._send(f"<pre>{traceback.format_exc()}</pre>", 500)
        finally:
            store.close()


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    config.ensure_dirs()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Factory Incident Engine UI  ->  http://{host}:{port}")
    print("(populate data first with `fie demo` if the dashboard is empty)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()
