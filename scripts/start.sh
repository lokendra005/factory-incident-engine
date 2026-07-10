#!/usr/bin/env bash
#
# Proper startup for the Factory Incident Engine web UI.
#   - verifies the package is importable
#   - checks the port is free
#   - builds the demo data on first run (or with --fresh)
#   - launches the control-room UI
#
# Usage: scripts/start.sh [--host H] [--port P] [--fresh]
#   --host   bind address           (default 127.0.0.1)
#   --port   port                    (default 8000)
#   --fresh  rebuild the data first  (runs `fie demo`)
#
set -euo pipefail

HOST="127.0.0.1"
PORT="8000"
FRESH=0

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --fresh) FRESH=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export FIE_DATA_DIR="${FIE_DATA_DIR:-$ROOT/data}"
export FIE_DB="${FIE_DB:-$FIE_DATA_DIR/plant.db}"
PY="${PYTHON:-python3}"

# 1) package importable?
if ! "$PY" -c "import fie" >/dev/null 2>&1; then
  echo "error: the 'fie' package is not importable." >&2
  echo "       install deps first:  pip install -r requirements.txt" >&2
  exit 1
fi

# 2) port free?
if command -v lsof >/dev/null 2>&1 && lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
  echo "error: port $PORT is already in use (pid $(lsof -ti "tcp:$PORT" | tr '\n' ' '))." >&2
  echo "       stop it:  scripts/stop.sh --port $PORT     or pick another --port" >&2
  exit 1
fi

# 3) data present? (rebuild on --fresh, missing DB, or an empty store)
need_data=0
[ "$FRESH" = 1 ] && need_data=1
[ -f "$FIE_DB" ] || need_data=1
if [ "$need_data" = 0 ]; then
  inc="$("$PY" -c "from fie.store import Store; s=Store(); print(s.counts()['incidents']); s.close()" 2>/dev/null || echo 0)"
  [ "${inc:-0}" -eq 0 ] 2>/dev/null && need_data=1
fi
if [ "$need_data" = 1 ]; then
  echo "==> building demo data (simulate → ingest → reconstruct)…"
  "$PY" -m fie.cli demo >/dev/null
fi

# 4) serve
echo "==> Factory Incident Engine  →  http://$HOST:$PORT   (Ctrl-C to stop)"
exec "$PY" -m fie.cli serve --host "$HOST" --port "$PORT"
