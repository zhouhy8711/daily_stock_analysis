#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="${DSA_SCREEN_SESSION:-dsa-api}"
HOST="${DSA_HOST:-0.0.0.0}"
PORT="${DSA_PORT:-8000}"
WEB_DEV_PORT="${DSA_WEB_DEV_PORT:-5173}"
PYTHON_BIN="${PYTHON:-python}"
LOG_FILE="${DSA_LOG_FILE:-/tmp/dsa-api-run.log}"
LOCAL_BASE_URL="http://127.0.0.1:${PORT}"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 127
  fi
}

kill_listeners() {
  local port="$1"
  local pids

  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return
  fi

  echo "Stopping processes listening on port ${port}: ${pids//$'\n'/ }"
  # shellcheck disable=SC2086
  kill $pids >/dev/null 2>&1 || true
  sleep 1

  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "Force stopping processes still listening on port ${port}: ${pids//$'\n'/ }"
    # shellcheck disable=SC2086
    kill -9 $pids >/dev/null 2>&1 || true
    sleep 1
  fi
}

wait_for_health() {
  local attempts=30
  local attempt
  local screen_list

  for attempt in $(seq 1 "$attempts"); do
    if curl -fsS --max-time 3 "${LOCAL_BASE_URL}/api/health" >/dev/null 2>&1; then
      return 0
    fi

    screen_list="$(screen -ls 2>/dev/null || true)"
    if [ "$attempt" -gt 5 ] && ! printf '%s\n' "$screen_list" | grep -q "[.]${SESSION_NAME}"; then
      echo "Service screen session exited before health check passed." >&2
      tail -80 "$LOG_FILE" >&2 || true
      return 1
    fi

    sleep 1
  done

  echo "Health check did not pass after ${attempts}s: ${LOCAL_BASE_URL}/api/health" >&2
  tail -80 "$LOG_FILE" >&2 || true
  return 1
}

verify_frontend_assets() {
  local html_file
  local assets
  local asset

  html_file="$(mktemp)"
  curl -fsS --max-time 10 "${LOCAL_BASE_URL}/" -o "$html_file"

  assets="$(grep -Eo '/assets/[^" ]+' "$html_file" | sort -u || true)"
  if [ -z "$assets" ]; then
    echo "Warning: no frontend assets found in ${LOCAL_BASE_URL}/" >&2
    rm -f "$html_file"
    return 0
  fi

  while IFS= read -r asset; do
    [ -z "$asset" ] && continue
    curl -fsSI --max-time 10 "${LOCAL_BASE_URL}${asset}" >/dev/null
  done <<< "$assets"

  rm -f "$html_file"
}

main() {
  require_cmd screen
  require_cmd lsof
  require_cmd curl
  require_cmd grep
  require_cmd sort
  require_cmd seq
  require_cmd "$PYTHON_BIN"

  echo "Restarting DSA Web/API on ${LOCAL_BASE_URL}"
  echo "Project root: ${ROOT_DIR}"
  echo "Log file: ${LOG_FILE}"

  screen -S "$SESSION_NAME" -X quit >/dev/null 2>&1 || true
  sleep 1

  kill_listeners "$PORT"
  kill_listeners "$WEB_DEV_PORT"

  mkdir -p "$(dirname "$LOG_FILE")"
  : > "$LOG_FILE"

  screen -dmS "$SESSION_NAME" bash -c '
    set -euo pipefail
    cd "$1"
    exec "$2" -u main.py --serve-only --host "$3" --port "$4" >> "$5" 2>&1
  ' bash "$ROOT_DIR" "$PYTHON_BIN" "$HOST" "$PORT" "$LOG_FILE"

  wait_for_health

  if lsof -tiTCP:"${WEB_DEV_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Unexpected listener remains on web dev port ${WEB_DEV_PORT}" >&2
    lsof -nP -iTCP:"${WEB_DEV_PORT}" -sTCP:LISTEN >&2 || true
    exit 1
  fi

  verify_frontend_assets

  echo "DSA restarted successfully."
  echo "URL: ${LOCAL_BASE_URL}/"
  echo "Session: ${SESSION_NAME}"
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN || true
}

main "$@"
