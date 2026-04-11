#!/usr/bin/env bash
set -euo pipefail

SERVER_NODE="${1:-edge-host-1}"
PORT="${2:-5000}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/../../.. && pwd)"
SRC_C="${ROOT_DIR}/Network-live-migration/scripts/workloads/tcp-howto.c"

REMOTE_SRC="/home/ubuntu/tcp-howto.c"
REMOTE_BIN="/tmp/tcp-howto"
OUT_PATH="/home/ubuntu/tcp_server.out"
PID_PATH="/home/ubuntu/tcp_server.pid"

echo "[tcp-server] Transferring tcp-howto.c to ${SERVER_NODE}..."
multipass transfer "${SRC_C}" "${SERVER_NODE}:${REMOTE_SRC}"

echo "[tcp-server] Compiling on ${SERVER_NODE}..."
multipass exec "${SERVER_NODE}" -- bash -lc "
  set -euo pipefail
  if ! command -v gcc >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y build-essential >/dev/null
  fi
  gcc -O2 -Wall -o ${REMOTE_BIN} ${REMOTE_SRC}
  chmod +x ${REMOTE_BIN}
"

echo "[tcp-server] Starting on ${SERVER_NODE} (port ${PORT})..."
multipass exec "${SERVER_NODE}" -- bash -lc "
  set -euo pipefail
  test -x ${REMOTE_BIN}
  # Avoid pkill -f with full args because it can match this launcher command.
  pkill -x 'tcp-howto' 2>/dev/null || true
  sudo pkill -x 'tcp-howto' 2>/dev/null || true
  sudo fuser -k ${PORT}/tcp 2>/dev/null || true
  rm -f ${PID_PATH}
  touch ${OUT_PATH} && chmod 664 ${OUT_PATH}
  nohup ${REMOTE_BIN} ${PORT} >> ${OUT_PATH} 2>&1 &
  pid=\$!
  echo \$pid > ${PID_PATH}
  sleep 0.2
  if ! kill -0 \$pid 2>/dev/null; then
    echo '[tcp-server] ERROR: server process died immediately' >&2
    tail -n 120 ${OUT_PATH} 2>/dev/null || true
    exit 1
  fi
"

echo "[tcp-server] Waiting for listen..."
for _ in $(seq 1 20); do
  if multipass exec "${SERVER_NODE}" -- bash -lc "ss -ltn | grep -q ':${PORT} '"; then
    echo "✓ TCP server listening on ${SERVER_NODE}:${PORT}"
    echo "[tcp-server] Logs: multipass exec ${SERVER_NODE} -- tail -f ${OUT_PATH}"
    exit 0
  fi
  sleep 0.5
done

echo "ERROR: TCP server did not start listening on ${SERVER_NODE}:${PORT}"
echo "[tcp-server] Last logs:"
multipass exec "${SERVER_NODE}" -- tail -n 80 "${OUT_PATH}" 2>/dev/null || true
exit 1
