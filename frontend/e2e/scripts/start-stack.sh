#!/usr/bin/env bash
# Start (or restart) what the browser tests need besides the web app:
# the FlowPilot API, the background worker and the local mail catcher.
#
#   frontend/e2e/scripts/start-stack.sh          # start / restart all three
#   frontend/e2e/scripts/start-stack.sh stop     # stop them
#
# Needs: Postgres (with pgvector) and Redis already running, backend/.venv,
# and backend/.env prepared for e2e (see frontend/e2e/README.md). The script
# migrates the database, then starts the processes in the background with
# their logs in $E2E_LOG_DIR (default /tmp/flowpilot-e2e-logs).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
BACKEND="$REPO/backend"
LOG_DIR="${E2E_LOG_DIR:-/tmp/flowpilot-e2e-logs}"
RUN_DIR="$LOG_DIR/pids"
PY="${E2E_PYTHON:-$BACKEND/.venv/bin/python}"
API_PORT="${E2E_API_PORT:-8000}"
mkdir -p "$LOG_DIR" "$RUN_DIR"

stop_one() {
  local name="$1" pidfile="$RUN_DIR/$1.pid"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
    echo "stopped $name"
  fi
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

stop_all() {
  stop_one api
  stop_one worker
  stop_one smtp
  stop_one llm
}

if [[ "${1:-}" == "stop" ]]; then
  stop_all
  exit 0
fi

stop_all
if port_busy "$API_PORT"; then
  echo "port $API_PORT is already in use by a process this script did not start; stop it first" >&2
  exit 1
fi

cd "$BACKEND"
echo "migrating database..."
ARCH40_CONTRACT=1 "$PY" -m alembic upgrade head >"$LOG_DIR/migrate.log" 2>&1 || {
  tail -20 "$LOG_DIR/migrate.log" >&2
  exit 1
}

if ! port_busy 1025; then
  nohup node "$HERE/../support/smtp-sink.mjs" >"$LOG_DIR/smtp.log" 2>&1 &
  echo $! >"$RUN_DIR/smtp.pid"
fi

# E2E_LLM=1: a deterministic local model stand-in (support/llm-mock.mjs) behind the sovereign
# edition's local-model setting, so extraction, verification, the review queue and the assistant
# run end to end without a provider key. Unset: no model, as in the sandbox before (F-063).
if [[ "${E2E_LLM:-}" == "1" ]]; then
  LLM_PORT="${E2E_LLM_PORT:-11434}"
  if ! port_busy "$LLM_PORT"; then
    E2E_LLM_PORT="$LLM_PORT" nohup node "$HERE/../support/llm-mock.mjs" >"$LOG_DIR/llm.log" 2>&1 &
    echo $! >"$RUN_DIR/llm.pid"
  fi
  export LOCAL_LLM_MODE=exclusive
  export LOCAL_LLM_BASE_URL="http://127.0.0.1:$LLM_PORT/v1"
  export LOCAL_LLM_MODEL="${E2E_LLM_MODEL:-flowpilot-e2e-local}"
  export LOCAL_LLM_TIMEOUT_SECONDS=30
fi

nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" >"$LOG_DIR/api.log" 2>&1 &
echo $! >"$RUN_DIR/api.pid"

nohup "$PY" -m app.worker --loop all --profile all --log-level INFO >"$LOG_DIR/worker.log" 2>&1 &
echo $! >"$RUN_DIR/worker.pid"

for _ in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:$API_PORT/api/v1/health" >/dev/null; then
    echo "API healthy on :$API_PORT (logs in $LOG_DIR)"
    exit 0
  fi
  if ! kill -0 "$(cat "$RUN_DIR/api.pid")" 2>/dev/null; then
    echo "the API exited during start-up:" >&2
    tail -30 "$LOG_DIR/api.log" >&2
    exit 1
  fi
  sleep 2
done
echo "the API did not become healthy in 180 s" >&2
tail -30 "$LOG_DIR/api.log" >&2
exit 1
