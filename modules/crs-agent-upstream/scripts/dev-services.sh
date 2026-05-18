#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
BACKEND_ENV_FILE="${ROOT_DIR}/backend/.env"
BACKEND_RUNTIME_ENV_FILE="${ROOT_DIR}/backend/.env.runtime"

BACKEND_PORT="${BACKEND_PORT:-9090}"
USER_PORT="${USER_PORT:-5170}"
ADMIN_PORT="${ADMIN_PORT:-5171}"
HOST="${HOST:-127.0.0.1}"
BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-${HOST}}"
BACKEND_PROD_BIND_HOST="${BACKEND_PROD_BIND_HOST:-0.0.0.0}"
API_PROXY_TARGET="${API_PROXY_TARGET:-http://${HOST}:${BACKEND_PORT}}"
BACKEND_RELOAD="${BACKEND_RELOAD:-0}"
BACKEND_WORKERS="${BACKEND_WORKERS:-4}"
BACKEND_VENV_PYTHON="${ROOT_DIR}/backend/.venv/bin/python"

if [[ -x "${BACKEND_VENV_PYTHON}" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${BACKEND_VENV_PYTHON}}"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-$(command -v python)}"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi
NPM_BIN="${NPM_BIN:-$(command -v npm)}"

mkdir -p "${LOG_DIR}"

usage() {
  cat <<'EOF'
Usage:
  bash ./scripts/dev-services.sh <start|stop|restart> <all|frontend|admin|backend> [dev|prod]

Ports:
  backend  : 9090
  frontend : 5170
  admin    : 5171

Modes:
  dev  : backend single worker, optional --reload
  prod : backend background start with --workers 4
EOF
}

require_bin() {
  local bin="$1"
  if ! command -v "${bin}" >/dev/null 2>&1; then
    echo "Missing required command: ${bin}" >&2
    exit 1
  fi
}

port_pids() {
  local port="$1"
  lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
}

is_port_listening() {
  local port="$1"
  lsof -tiTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

stop_port() {
  local port="$1"
  local name="$2"
  local pids

  pids="$(port_pids "${port}")"
  if [[ -z "${pids}" ]]; then
    echo "${name} is not running on :${port}"
    return 0
  fi

  echo "Stopping ${name} on :${port} (${pids//$'\n'/ })"
  kill ${pids} 2>/dev/null || true

  for _ in {1..10}; do
    if ! is_port_listening "${port}"; then
      echo "${name} stopped"
      return 0
    fi
    sleep 1
  done

  pids="$(port_pids "${port}")"
  if [[ -n "${pids}" ]]; then
    echo "Force stopping ${name} on :${port} (${pids//$'\n'/ })"
    kill -9 ${pids} 2>/dev/null || true
  fi
}

stop_matching_processes() {
  local pattern="$1"
  local name="$2"
  local pids

  pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    return 0
  fi

  echo "Stopping ${name} process (${pids//$'\n'/ })"
  kill ${pids} 2>/dev/null || true
  sleep 1

  pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Force stopping ${name} process (${pids//$'\n'/ })"
    kill -9 ${pids} 2>/dev/null || true
  fi
}

wait_for_port() {
  local port="$1"
  local name="$2"
  local log_file="$3"

  for _ in {1..30}; do
    if is_port_listening "${port}"; then
      echo "${name} started: http://${HOST}:${port}"
      echo "log: ${log_file}"
      return 0
    fi
    sleep 1
  done

  echo "${name} failed to start, check log: ${log_file}" >&2
  return 1
}

launch_in_background() {
  local log_file="$1"
  shift

  LAUNCH_LOG_FILE="${log_file}" "${PYTHON_BIN:-python3}" - "$@" <<'PY'
import os
import subprocess
import sys

log_file = os.environ["LAUNCH_LOG_FILE"]
argv = sys.argv[1:]
with open(log_file, "ab", buffering=0) as log:
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
PY
}

stop_backend_processes() {
  stop_matching_processes "uvicorn app.main:app --host ${BACKEND_BIND_HOST} --port ${BACKEND_PORT}" "backend"
  if [[ "${BACKEND_PROD_BIND_HOST}" != "${BACKEND_BIND_HOST}" ]]; then
    stop_matching_processes "uvicorn app.main:app --host ${BACKEND_PROD_BIND_HOST} --port ${BACKEND_PORT}" "backend"
  fi
}

start_backend() {
  local mode="${1:-dev}"
  require_bin "${PYTHON_BIN}"
  stop_port "${BACKEND_PORT}" "backend"
  stop_backend_processes

  local log_file="${LOG_DIR}/backend.log"
  local previous_dir="${PWD}"
  local bind_host="${BACKEND_BIND_HOST}"
  local worker_count="1"

  if [[ "${mode}" == "prod" ]]; then
    bind_host="${BACKEND_PROD_BIND_HOST}"
    worker_count="${BACKEND_WORKERS}"
  fi

  echo "Starting backend on :${BACKEND_PORT} (mode=${mode}, workers=${worker_count})"
  cd "${ROOT_DIR}/backend"
  if [[ "${mode}" == "prod" ]]; then
    launch_in_background "${log_file}" env PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}" \
      "${PYTHON_BIN}" -m uvicorn app.main:app \
      --host "${bind_host}" \
      --port "${BACKEND_PORT}" \
      --workers "${BACKEND_WORKERS}"
  elif [[ "${BACKEND_RELOAD}" == "1" ]]; then
    launch_in_background "${log_file}" env PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}" \
      "${PYTHON_BIN}" -m uvicorn app.main:app \
      --host "${bind_host}" \
      --port "${BACKEND_PORT}" \
      --reload
  else
    launch_in_background "${log_file}" env PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}" \
      "${PYTHON_BIN}" -m uvicorn app.main:app \
      --host "${bind_host}" \
      --port "${BACKEND_PORT}"
  fi
  cd "${previous_dir}"

  wait_for_port "${BACKEND_PORT}" "backend" "${log_file}"
}

start_frontend() {
  require_bin "${NPM_BIN}"
  stop_port "${USER_PORT}" "frontend"

  local log_file="${LOG_DIR}/frontend.log"
  echo "Starting frontend on :${USER_PORT}"
  (
    cd "${ROOT_DIR}/frontend/user"
    launch_in_background "${log_file}" env CI=1 VITE_API_PROXY_TARGET="${API_PROXY_TARGET}" \
      "${NPM_BIN}" run dev -- --host "${HOST}" --port "${USER_PORT}" --strictPort \
  )

  wait_for_port "${USER_PORT}" "frontend" "${log_file}"
}

start_admin() {
  require_bin "${NPM_BIN}"
  stop_port "${ADMIN_PORT}" "admin"

  local log_file="${LOG_DIR}/admin.log"
  echo "Starting admin on :${ADMIN_PORT}"
  (
    cd "${ROOT_DIR}/frontend/admin"
    launch_in_background "${log_file}" env CI=1 VITE_API_PROXY_TARGET="${API_PROXY_TARGET}" \
      "${NPM_BIN}" run dev -- --host "${HOST}" --port "${ADMIN_PORT}" --strictPort \
  )

  wait_for_port "${ADMIN_PORT}" "admin" "${log_file}"
}

start_target() {
  local target="$1"
  local mode="${2:-dev}"
  case "${target}" in
    all)
      start_backend "${mode}"
      start_frontend
      start_admin
      ;;
    frontend)
      start_frontend
      ;;
    admin)
      start_admin
      ;;
    backend)
      start_backend "${mode}"
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

stop_target() {
  local target="$1"
  case "${target}" in
    all)
      stop_port "${ADMIN_PORT}" "admin"
      stop_port "${USER_PORT}" "frontend"
      stop_port "${BACKEND_PORT}" "backend"
      ;;
    frontend)
      stop_port "${USER_PORT}" "frontend"
      ;;
    admin)
      stop_port "${ADMIN_PORT}" "admin"
      ;;
    backend)
      stop_port "${BACKEND_PORT}" "backend"
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

restart_target() {
  local target="$1"
  local mode="${2:-dev}"
  stop_target "${target}"
  start_target "${target}" "${mode}"
}

ACTION="${1:-}"
TARGET="${2:-}"
MODE="${3:-dev}"

if [[ -n "${MODE}" && "${MODE}" != "dev" && "${MODE}" != "prod" ]]; then
  usage
  exit 1
fi

case "${ACTION}" in
  start)
    start_target "${TARGET}" "${MODE}"
    ;;
  stop)
    stop_target "${TARGET}"
    ;;
  restart)
    restart_target "${TARGET}" "${MODE}"
    ;;
  *)
    usage
    exit 1
    ;;
esac
