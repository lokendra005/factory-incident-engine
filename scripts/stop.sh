#!/usr/bin/env bash
#
# Stop the Factory Incident Engine UI server.
# Usage: scripts/stop.sh [--port P]   (default 8000)
#
set -euo pipefail

PORT="8000"
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?}"; shift 2 ;;
    -h|--help) echo "Usage: scripts/stop.sh [--port P]"; exit 0 ;;
    *) shift ;;
  esac
done

if ! command -v lsof >/dev/null 2>&1; then
  echo "lsof not available; find and kill the process on port $PORT manually." >&2
  exit 1
fi

pids="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
if [ -z "$pids" ]; then
  echo "no server running on port $PORT."
  exit 0
fi

echo "stopping pid(s): $pids"
kill $pids 2>/dev/null || true
sleep 1
pids="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
[ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
echo "stopped."
