"""Zero-dependency web UI on the stdlib http.server.

Deliberately no web framework: the whole engine runs with only pydantic +
jinja2, so `fie serve` works straight after `pip install -r requirements.txt`
with nothing else to provision. Read-only over the store + captured runs.
"""
from __future__ import annotations

import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__, config
from ..models import EvidenceBundle
from ..reliability import assess
from ..store import Store

_TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html"]),
)

CATEGORY_COLOR = {
    "cooling_degradation": "#4fc3f7", "sensor_fault": "#ce93d8",
    "bearing_wear": "#ffb74d", "tool_wear": "#fff176", "overload": "#ff8a65",
    "operator_config": "#81c784", "no_incident": "#90a4ae", "unknown": "#e57373",
}
_env.globals["cat_color"] = lambda c: CATEGORY_COLOR.get(c, "#90a4ae")
_env.globals["version"] = __version__


def _reliability_for(store: Store, asset: str, start: str, end: str):
    bundle = EvidenceBundle(
        asset=asset, window_start=start, window_end=end,
        readings=store.query_readings(asset, start, end),
        maintenance=store.query_maintenance(asset, start, end),
        mes=store.query_mes(asset, start, end),
    )
    return assess(bundle)


def render_dashboard(store: Store) -> str:
    incidents = store.list_incidents()
    rel_rows = []
    seen = set()
    for r in incidents:
        key = (r.asset, r.window_start)
        if key in seen:
            continue
        seen.add(key)
        rel = _reliability_for(store, r.asset, r.window_start, r.window_end)
        rel_rows.append({"asset": r.asset, "start": r.window_start[:16], "rel": rel})
    return _env.get_template("dashboard.html").render(
        counts=store.counts(), dlq=store.dlq_counts(),
        drift=[dict(d) for d in store.drift_items()],
        incidents=incidents, rel_rows=rel_rows,
    )


def render_incident(store: Store, incident_id: str) -> str:
    report = store.get_incident(incident_id)
    if report is None:
        return _env.get_template("notfound.html").render(what=incident_id)
    rel = _reliability_for(store, report.asset, report.window_start, report.window_end)
    return _env.get_template("incident.html").render(r=report, rel=rel)


def render_regression(store: Store) -> str:
    from ..eval import build_golden
    from ..replay import run_regression
    build_golden()
    rep = run_regression("rule-based/1.1.0", "rule-based/1.2.0")
    return _env.get_template("regression.html").render(rep=rep)


class Handler(BaseHTTPRequestHandler):
    server_version = f"FIE/{__version__}"

    def log_message(self, *a):   # keep the console clean
        pass

    def _send(self, body: str, status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        store = Store()
        try:
            if path == "/":
                self._send(render_dashboard(store))
            elif path == "/regression":
                self._send(render_regression(store))
            elif path.startswith("/incident/"):
                self._send(render_incident(store, path.split("/incident/", 1)[1]))
            elif path == "/healthz":
                self._send("ok")
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
