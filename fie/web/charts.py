"""Server-rendered inline SVG line charts.

Rendered on the server (not client JS) so the telemetry is always visible, works
without JavaScript, and is reliably screenshot-able. Each chart plots one signal
over the incident window with its nominal reference line and markers for MES
events — so a viewer literally *sees* the failure signature (e.g. coolant flow
collapsing while temperature climbs).
"""
from __future__ import annotations

from datetime import datetime

from .. import config

W, H = 330, 132
PAD_L, PAD_R, PAD_T, PAD_B = 10, 10, 24, 16

SIGNAL_LABEL = {
    "spindle_temp_c": "Spindle temp (°C)",
    "coolant_flow_lpm": "Coolant flow (L/min)",
    "vibration_mm_s": "Vibration (mm/s)",
    "spindle_load_pct": "Spindle load (%)",
    "defect_rate_pct": "Defect rate (%)",
    "throughput_pph": "Throughput (pph)",
}


def _t(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


def signal_chart(signal: str, readings: list, events: list,
                 accent: str = "#4fc3f7") -> str:
    """readings: list[TelemetryReading] for ONE signal (sorted). events: mes."""
    label = SIGNAL_LABEL.get(signal, signal)
    pts = [(r.ts, r.value) for r in readings if r.signal == signal]
    if len(pts) < 2:
        return (f'<div class="chart empty"><span class="clabel">{label}</span>'
                f'<div class="muted" style="padding:24px 8px">no data in window</div></div>')

    t0, tN = _t(pts[0][0]), _t(pts[-1][0])
    span = max(tN - t0, 1.0)
    vals = [v for _, v in pts]
    nominal = config.NOMINAL.get(signal)
    lo = min(vals + ([nominal] if nominal is not None else []))
    hi = max(vals + ([nominal] if nominal is not None else []))
    if hi - lo < 1e-6:
        hi = lo + 1.0
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    def x(ts):
        return PAD_L + (_t(ts) - t0) / span * plot_w

    def y(v):
        return PAD_T + (hi - v) / (hi - lo) * plot_h

    poly = " ".join(f"{x(ts):.1f},{y(v):.1f}" for ts, v in pts)

    parts = [f'<div class="chart"><svg viewBox="0 0 {W} {H}" '
             f'preserveAspectRatio="none" role="img" aria-label="{label}">']
    # nominal reference line
    if nominal is not None and lo <= nominal <= hi:
        yn = y(nominal)
        parts.append(f'<line x1="{PAD_L}" y1="{yn:.1f}" x2="{W-PAD_R}" y2="{yn:.1f}" '
                     f'stroke="#3a4657" stroke-dasharray="3 3" stroke-width="1"/>')
    # MES event markers
    for e in events:
        try:
            ex = x(e.ts)
        except Exception:
            continue
        if not (PAD_L <= ex <= W - PAD_R):
            continue
        col = "#ff6b6b" if e.event == "shutdown" else "#ffb74d"
        parts.append(f'<line x1="{ex:.1f}" y1="{PAD_T}" x2="{ex:.1f}" y2="{H-PAD_B}" '
                     f'stroke="{col}" stroke-width="1" opacity="0.55"/>')
    # the trace
    parts.append(f'<polyline fill="none" stroke="{accent}" stroke-width="1.8" '
                 f'points="{poly}"/>')
    # end dot
    lx, lv = pts[-1]
    parts.append(f'<circle cx="{x(lx):.1f}" cy="{y(lv):.1f}" r="2.6" fill="{accent}"/>')
    parts.append("</svg>")
    parts.append(f'<div class="chart-head"><span class="clabel">{label}</span>'
                 f'<span class="cval mono">{vals[-1]:.1f}</span></div></div>')
    return "".join(parts)


def incident_charts(signals: list[str], readings: list, events: list) -> str:
    return "".join(signal_chart(s, readings, events) for s in signals)
