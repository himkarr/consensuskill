#!/usr/bin/env bash
# Local development stack for consensuskill.
#
#   scripts/dev.sh up       start redis + engine + gateway + frontend preview
#   scripts/dev.sh down     stop everything started by `up`
#   scripts/dev.sh status   show what is running and where the logs are
#   scripts/dev.sh logs     tail every service log
#
# The critical bit this script exists for: uvicorn MUST come from .venv.
# A bare `uvicorn` on PATH resolves to the system Python, which has no
# fastapi/redis installed, and dies with ModuleNotFoundError.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGDIR="$ROOT/.run/logs"
PIDDIR="$ROOT/.run/pids"
mkdir -p "$LOGDIR" "$PIDDIR"

# Optional gitignored env (REDIS_URL, SUPABASE_DB_URL, ...) for every service.
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

VENV_UVICORN="$ROOT/.venv/bin/uvicorn"
REDIS_BIN="$(command -v redis-server || true)"
REDIS_PORT=6379
ENGINE_PORT=8001
GATEWAY_PORT=8000
FRONTEND_PORT="${FRONTEND_PORT:-4173}" # override if another project squats the port
FRONTEND_URL="http://127.0.0.1:$FRONTEND_PORT"

# --- helpers -----------------------------------------------------------------

port_up() { curl -s -m 1 "http://127.0.0.1:$1/" >/dev/null 2>&1; }

# port_up alone can be fooled by an unrelated process on the same port, so
# these verify the *right* app is answering before we skip starting it.
engine_up() {
  curl -s -m 1 "http://127.0.0.1:$ENGINE_PORT/health" 2>/dev/null | grep -q '"service":"engine"'
}
gateway_up() {
  curl -s -m 1 "http://127.0.0.1:$GATEWAY_PORT/health" 2>/dev/null | grep -q '"service":"gateway"'
}
frontend_up() { curl -s -m 1 "$FRONTEND_URL/" 2>/dev/null | grep -q "ConsensusKill"; }

redis_up() { redis-cli -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; }

wait_for() { # wait_for <name> <check-cmd...>
  local name="$1"; shift
  for _ in $(seq 1 40); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 0.25
  done
  echo "ERROR: $name did not come up in 10s" >&2
  return 1
}

start_bg() { # start_bg <name> <cmd...>
  local name="$1"; shift
  setsid nohup "$@" >"$LOGDIR/$name.log" 2>&1 </dev/null &
  echo $! >"$PIDDIR/$name.pid"
}

stop_one() { # stop_one <name>
  local name="$1" pid
  [ -f "$PIDDIR/$name.pid" ] || return 0
  pid="$(cat "$PIDDIR/$name.pid")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 0.3
    kill -9 "$pid" 2>/dev/null || true
    echo "stopped $name (pid $pid)"
  fi
  rm -f "$PIDDIR/$name.pid"
}

# --- commands ----------------------------------------------------------------

up() {
  if [ ! -x "$VENV_UVICORN" ]; then
    echo "ERROR: $VENV_UVICORN not found. Create it first:" >&2
    echo "  uv venv .venv --python 3.11 && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt" >&2
    exit 1
  fi
  if [ -z "$REDIS_BIN" ]; then
    echo "ERROR: redis-server not on PATH (apt install redis-server, or add your build's src/ dir to PATH)." >&2
    exit 1
  fi
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    echo "ERROR: frontend/node_modules missing. Run: cd frontend && npm install" >&2
    exit 1
  fi

  cd "$ROOT"

  echo "redis   ..."
  if ! redis_up; then
    "$REDIS_BIN" --port "$REDIS_PORT" --daemonize no \
      --dir "$LOGDIR" --logfile "$LOGDIR/redis.log" --save '' >/dev/null 2>&1 &
    echo $! >"$PIDDIR/redis.pid"
    wait_for redis redis_up
  else
    echo "  already running on :$REDIS_PORT"
  fi

  echo "engine  ..."
  if engine_up; then
    echo "  already running on :$ENGINE_PORT"
  elif port_up "$ENGINE_PORT"; then
    echo "ERROR: :$ENGINE_PORT answers HTTP but is not the engine (foreign process)." >&2
    exit 1
  else
    start_bg engine "$VENV_UVICORN" engine.engine:app --host 127.0.0.1 --port "$ENGINE_PORT" --log-level warning
    wait_for engine curl -sf "http://127.0.0.1:$ENGINE_PORT/health"
  fi

  echo "gateway ..."
  if gateway_up; then
    echo "  already running on :$GATEWAY_PORT"
  elif port_up "$GATEWAY_PORT"; then
    echo "ERROR: :$GATEWAY_PORT answers HTTP but is not the gateway (foreign process)." >&2
    exit 1
  else
    start_bg gateway "$VENV_UVICORN" gateway.app.main:app --host 127.0.0.1 --port "$GATEWAY_PORT" --log-level warning
    wait_for gateway curl -sf "http://127.0.0.1:$GATEWAY_PORT/health"
  fi

  echo "frontend ..."
  if frontend_up; then
    echo "  already running on :$FRONTEND_PORT"
  elif port_up "$FRONTEND_PORT"; then
    echo "ERROR: :$FRONTEND_PORT is served by another app (not ConsensusKill)." >&2
    echo "  hint: FRONTEND_PORT=4199 scripts/dev.sh up" >&2
    echo "        SMOKE_URL=http://127.0.0.1:4199 npm --prefix frontend run smoke" >&2
    exit 1
  else
    (cd "$ROOT/frontend" && npm run build) || exit 1
    start_bg frontend npm --prefix "$ROOT/frontend" run preview -- --port "$FRONTEND_PORT" --host 127.0.0.1
    wait_for frontend curl -sf "$FRONTEND_URL/"
  fi

  echo
  echo "UP:"
  echo "  game     $FRONTEND_URL     (Vite preview, proxies /ws -> gateway)"
  echo "  gateway  http://127.0.0.1:$GATEWAY_PORT/health"
  echo "  engine   http://127.0.0.1:$ENGINE_PORT/health"
  echo "  redis    redis-cli -p $REDIS_PORT ping"
  echo
  echo "Run the browser smoke test:  cd frontend && npm run smoke"
}

down() {
  stop_one frontend
  stop_one gateway
  stop_one engine
  stop_one redis
  echo "all stopped"
}

status() {
  if redis_up; then echo "  redis :$REDIS_PORT  UP"; else echo "  redis :$REDIS_PORT  down"; fi
  for pair in "engine:$ENGINE_PORT" "gateway:$GATEWAY_PORT" "frontend:$FRONTEND_PORT"; do
    name="${pair%%:*}"; port="${pair##*:}"
    if port_up "$port"; then echo "  $name :$port  UP"; else echo "  $name :$port  down"; fi
  done
  echo "  logs: $LOGDIR"
}

case "${1:-up}" in
  up) up ;;
  down) down ;;
  status) status ;;
  logs) tail -n 40 "$LOGDIR"/*.log ;;
  *) echo "usage: $0 {up|down|status|logs}" >&2; exit 2 ;;
esac
