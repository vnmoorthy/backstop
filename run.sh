#!/usr/bin/env bash
# Backstop — bulletproof one-command launcher for the live demo.
#   ./run.sh
# Frees the port if a stale server is holding it (the #1 cause of "it does
# nothing"), installs deps if missing, starts the server, opens the dashboard.
# NOT `set -e` — a non-fatal check must never silently abort the launch.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/orchestrator"

PORT="${PORT:-8000}"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "ERROR: no python found. Install Python 3.9+ and re-run." >&2
  exit 1
fi
echo "▸ python: $PY ($($PY --version 2>&1))"

# FREE THE PORT: kill any stale server still holding it, so the new one can bind.
STALE="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$STALE" ]; then
  echo "▸ port $PORT was busy — clearing stale server (pid $STALE)…"
  # shellcheck disable=SC2086
  kill -9 $STALE 2>/dev/null || true
  sleep 1
fi

# install deps only if missing
if ! "$PY" -c "import fastapi, torch, reportlab, websockets" >/dev/null 2>&1; then
  echo "▸ installing dependencies (one time, ~1 min)…"
  "$PY" -m pip install -q -r requirements.txt || { echo "ERROR: pip install failed." >&2; exit 1; }
fi

# PAVO sanity (non-fatal — informational only)
if "$PY" -c "from backstop.pavo import MaskedPAVORouter; assert MaskedPAVORouter().n_params==85041" >/dev/null 2>&1; then
  echo "▸ PAVO router OK (85,041 params)"
else
  echo "▸ WARNING: PAVO sanity check failed — continuing (the demo may still run)."
fi

echo ""
echo "  ┌──────────────────────────────────────────────────┐"
echo "  │  BACKSTOP is starting…                            │"
echo "  │  → OPEN:  http://localhost:$PORT                      │"
echo "  │  → if the page looks blank/old: HARD-REFRESH      │"
echo "  │           (Cmd+Shift+R on Mac, Ctrl+Shift+R else) │"
echo "  │  → Ctrl-C to stop.                                │"
echo "  └──────────────────────────────────────────────────┘"
echo ""

# open the dashboard once the server is up
( sleep 3; (command -v open >/dev/null && open "http://localhost:$PORT") || \
           (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT") || true ) &

exec "$PY" -m backstop.server
