#!/bin/sh
# Manual/reference start for Render free tier: one process = gateway + engine.
# (render.yaml cannot use startCommand - docker runtime forbids it - so the
# image CMD runs uvicorn with the same env vars pinned in the blueprint.)
#
# Render's free plan = 750 instance-hours/month = exactly one always-on web
# service, so the round engine runs in-process via EMBEDDED_ENGINE=1.
# Redis is Upstash (free): no blocking reads, tiny monthly command budget -
# the engine values below keep a mostly-idle app well under 500K cmds/month.
set -eu

export EMBEDDED_ENGINE=1
export PORT="${PORT:-10000}"

# Hosted Redis tuning -------------------------------------------------------
export ENGINE_BLOCK_MS=0 # 0/negative -> XREADGROUP without BLOCK (Upstash forbids it)
export ENGINE_EMPTY_POLL_DELAY=30 # safety-net poll if a wake ping is ever missed
export ENGINE_RESCAN_SECONDS=30 # deadline-cache reconciliation cadence
export ENGINE_IDLE_TICK_SECONDS=60 # timer rescan cadence while no rooms exist
export ENGINE_SWEEP_SECONDS=300 # empty-room sweeper

# Fallbacks; REDIS_URL itself is pasted into the Render dashboard.
export ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-*}"
export INSTANCE_ID="${INSTANCE_ID:-render-$(hostname)}"

# Pick uvicorn: explicit override -> local .venv (dev machines) -> PATH
# (the Docker image installs uvicorn onto PATH). A bare `uvicorn` on a dev
# box often resolves to a system Python without the dependencies.
if [ -z "${UVICORN:-}" ]; then
  if [ -x .venv/bin/uvicorn ]; then
    UVICORN=.venv/bin/uvicorn
  else
    UVICORN=uvicorn
  fi
fi

exec "$UVICORN" gateway.app.main:app --host 0.0.0.0 --port "$PORT" --log-level warning
