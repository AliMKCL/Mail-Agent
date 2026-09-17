#!/usr/bin/env bash
#
# run_all.sh — start (or stop) the Mail Agent Python services in dependency order.
#
# Dependency order (Spec 2.2, acyclic):
#
#     database (8030)
#         |
#         +--> accounts (8010)      +--> vector_db (8040)
#                   |                         |
#                   +------------+------------+
#                                |
#                          user_data (8020)
#                                |
#                             mcp http (8050)
#                                |
#                           gateway (8000)
#
# Each stage is gated on the previous stage answering GET /health before the next
# one is launched. Failure to come up is fatal (nonzero exit).
#
# NOTE: The Go services on :8001 (sync) and :8002 (rate limiter) and Ollama on
# :11434 are managed separately and are NOT started or stopped by this script.
#
# Usage:
#     bash scripts/run_all.sh            # start everything
#     bash scripts/run_all.sh --stop     # tear everything down
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$REPO_ROOT/.run"

# name | module path | port
SERVICES=(
  "database|backend.services.database.app|8030"
  "accounts|backend.services.accounts.app|8010"
  "vector_db|backend.services.vector_db.app|8040"
  "user_data|backend.services.user_data.app|8020"
  "mcp|backend.services.mcp.http_app|8050"
  "gateway|backend.gateway.app|8000"
)

# Stages are started in order; every service inside a stage starts in parallel and
# the whole stage is health-gated before the next stage begins.
STAGES=(
  "database"
  "accounts vector_db"
  "user_data"
  "mcp"
  "gateway"
)

HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-40}"   # seconds to wait per service
HEALTH_INTERVAL="${HEALTH_INTERVAL:-0.5}"

log()  { printf '[run_all] %s\n' "$*"; }
warn() { printf '[run_all] WARN: %s\n' "$*" >&2; }
die()  { printf '[run_all] ERROR: %s\n' "$*" >&2; exit 1; }

service_field() {
  # service_field <name> <1=module|2=port>
  local name="$1" idx="$2" entry
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r n m p <<<"$entry"
    if [[ "$n" == "$name" ]]; then
      case "$idx" in
        1) printf '%s' "$m" ;;
        2) printf '%s' "$p" ;;
      esac
      return 0
    fi
  done
  return 1
}

module_exists() {
  # The Gateway and the MCP HTTP app are created in later phases. Report them as
  # not-yet-available instead of crashing.
  local module="$1"
  ( cd "$REPO_ROOT" && uv run python -c "
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('$module') else 1)
" ) >/dev/null 2>&1
}

wait_healthy() {
  # wait_healthy <name> <port> — poll GET /health with bounded backoff.
  local name="$1" port="$2"
  local pid_file="$RUN_DIR/$name.pid"
  local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))

  while :; do
    if curl -fsS --max-time 2 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      log "$name is healthy on :$port"
      return 0
    fi
    if [[ -f "$pid_file" ]] && ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      warn "$name exited early; see $RUN_DIR/$name.log"
      return 1
    fi
    if (( $(date +%s) >= deadline )); then
      return 1
    fi
    sleep "$HEALTH_INTERVAL"
  done
}

start_service() {
  # start_service <name>
  local name="$1" module port pid_file log_file
  module="$(service_field "$name" 1)" || die "unknown service: $name"
  port="$(service_field "$name" 2)"
  pid_file="$RUN_DIR/$name.pid"
  log_file="$RUN_DIR/$name.log"

  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    log "$name already running (pid $(cat "$pid_file"))"
    return 0
  fi

  if ! module_exists "$module"; then
    warn "$name: module '$module' does not exist yet — not started (created in a later phase)"
    return 2
  fi

  log "starting $name ($module) on :$port"
  ( cd "$REPO_ROOT" && exec uv run python -m "$module" ) >"$log_file" 2>&1 &
  echo $! >"$pid_file"
  return 0
}

stop_all() {
  local pid_file name pid stopped=0
  if [[ ! -d "$RUN_DIR" ]]; then
    log "nothing to stop (no $RUN_DIR)"
    return 0
  fi
  shopt -s nullglob
  for pid_file in "$RUN_DIR"/*.pid; do
    name="$(basename "$pid_file" .pid)"
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      log "stopping $name (pid $pid)"
      kill "$pid" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.3
      done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
      stopped=$((stopped + 1))
    fi
    rm -f "$pid_file"
  done
  shopt -u nullglob
  log "stopped $stopped service(s)"
  return 0
}

start_all() {
  mkdir -p "$RUN_DIR"
  local stage name rc started failed_gate=0
  for stage in "${STAGES[@]}"; do
    started=()
    for name in $stage; do
      start_service "$name"
      rc=$?
      if [[ $rc -eq 0 ]]; then
        started+=("$name")
      elif [[ $rc -ne 2 ]]; then
        stop_all
        die "failed to launch $name"
      fi
    done
    for name in "${started[@]:-}"; do
      [[ -z "$name" ]] && continue
      if ! wait_healthy "$name" "$(service_field "$name" 2)"; then
        warn "$name never became healthy within ${HEALTH_TIMEOUT}s"
        failed_gate=1
      fi
    done
    if [[ $failed_gate -eq 1 ]]; then
      stop_all
      die "aborting: a service in stage '$stage' never became healthy"
    fi
  done
  log "all available services are up. pids in $RUN_DIR"
  log "reminder: Go sync (:8001), rate limiter (:8002) and Ollama (:11434) are managed separately"
}

case "${1:-start}" in
  --stop|stop)
    stop_all
    ;;
  start)
    start_all
    ;;
  *)
    die "unknown argument: $1 (expected 'start' or '--stop')"
    ;;
esac
