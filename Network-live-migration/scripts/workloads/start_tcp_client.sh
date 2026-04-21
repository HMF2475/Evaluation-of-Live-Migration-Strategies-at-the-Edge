#!/usr/bin/env bash
set -euo pipefail

CLIENT_NODE="${1:-edge-node-1}"
SERVER="${2:-edge-host-1}"   # node name or IP
PORT="${3:-5000}"

TCP_VIP="${TCP_VIP:-10.22.132.250}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"/../../.. && pwd)"
SRC_C="${ROOT_DIR}/Network-live-migration/scripts/workloads/tcp-howto.c"

REMOTE_SRC="/home/ubuntu/tcp-howto.c"
REMOTE_BIN="/tmp/tcp-howto"

OUT_PATH="/home/ubuntu/tcp_client.out"
PID_PATH="/home/ubuntu/tcp_client.pid"
LEGACY_PID_PATH="/home/ubuntu/client.pid"
VIP_PATH="/home/ubuntu/tcp_vip.txt"
ENDPOINT_PATH="/home/ubuntu/tcp_server_endpoint.txt"

if [[ "${SERVER}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  SERVER_IP="${SERVER}"
else
  SERVER_IP="$(multipass exec "${SERVER}" -- bash -lc "hostname -I | awk '{print \$1}'")"
fi

echo "[tcp-client] Server endpoint: ${SERVER_IP}:${PORT}"
echo "[tcp-client] Client VIP: ${TCP_VIP}"

echo "[tcp-client] Transferring tcp-howto.c to ${CLIENT_NODE}..."
multipass transfer "${SRC_C}" "${CLIENT_NODE}:${REMOTE_SRC}"

echo "[tcp-client] Compiling on ${CLIENT_NODE}..."
multipass exec "${CLIENT_NODE}" -- bash -lc "
  set -euo pipefail
  if ! command -v gcc >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y build-essential >/dev/null
  fi
  gcc -O2 -Wall -o ${REMOTE_BIN} ${REMOTE_SRC}
  chmod +x ${REMOTE_BIN}
"

echo "[tcp-client] Ensuring VIP ${TCP_VIP} is present on ${CLIENT_NODE}..."
multipass exec "${CLIENT_NODE}" -- bash -lc "
  set -euo pipefail
  iface=\$(ip -o route get ${SERVER_IP} | awk '{for(i=1;i<=NF;i++) if(\$i==\"dev\") {print \$(i+1); exit}}')
  test -n \"\$iface\"
  sudo ip addr add ${TCP_VIP}/32 dev "\$iface" 2>/dev/null || true
  sudo arping -c 3 -U -I "\$iface" ${TCP_VIP} 2>/dev/null || true
  echo \"${TCP_VIP}\" > ${VIP_PATH}
  echo \"${SERVER_IP}:${PORT}\" > ${ENDPOINT_PATH}
"

echo "[tcp-client] Starting on ${CLIENT_NODE}..."
multipass exec "${CLIENT_NODE}" -- bash -lc "
  set -euo pipefail
  test -x ${REMOTE_BIN}
  # Avoid pkill -f with full args because it can match this launcher command.
  pkill -x 'tcp-howto' 2>/dev/null || true
  sudo pkill -x 'tcp-howto' 2>/dev/null || true
  rm -f ${PID_PATH} ${LEGACY_PID_PATH}
  touch ${OUT_PATH} && chmod 664 ${OUT_PATH}
  nohup env LOCAL_IP=${TCP_VIP} ${REMOTE_BIN} ${SERVER_IP} ${PORT} >> ${OUT_PATH} 2>&1 &
  pid=\$!
  echo \$pid > ${PID_PATH}
  cp ${PID_PATH} ${LEGACY_PID_PATH}
  sleep 0.2
  if ! kill -0 \$pid 2>/dev/null; then
    echo '[tcp-client] ERROR: client process died immediately' >&2
    echo '[tcp-client] Last logs:' >&2
    tail -n 120 ${OUT_PATH} 2>/dev/null || true
    exit 1
  fi
"


echo "[tcp-client] Waiting for established TCP connection..."
for _ in $(seq 1 120); do
  if multipass exec "${CLIENT_NODE}" -- bash -lc "ss -tn state established | grep -q ':${PORT} '"; then
    echo "✓ TCP client connected (PID: $(multipass exec "${CLIENT_NODE}" -- cat "${PID_PATH}")")"
    echo "[tcp-client] Logs: multipass exec ${CLIENT_NODE} -- tail -f ${OUT_PATH}"
    exit 0
  fi
  sleep 0.5
done

echo "ERROR: TCP client did not connect to ${SERVER_IP}:${PORT}"
echo "[tcp-client] Last logs:"
multipass exec "${CLIENT_NODE}" -- tail -n 120 "${OUT_PATH}" 2>/dev/null || true
exit 1
