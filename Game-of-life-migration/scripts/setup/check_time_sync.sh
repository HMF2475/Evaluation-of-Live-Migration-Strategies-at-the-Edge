#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash Game-of-life-migration/scripts/setup/check_time_sync.sh [edge-node-1] [edge-node-2] [edge-host-1] ...

Prints a quick clock sync report between host and each Multipass VM.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -eq 0 ]]; then
  set -- edge-node-1 edge-node-2 edge-host-1
fi

host_epoch="$(date +%s)"
host_iso="$(date -Is)"
echo "Host: ${host_iso} (epoch=${host_epoch})"

for node in "$@"; do
  node_epoch="$(multipass exec "$node" -- bash -lc 'date +%s' | tr -d '\r' || true)"
  node_iso="$(multipass exec "$node" -- bash -lc 'date -Is' | tr -d '\r' || true)"
  ntp_sync="$(multipass exec "$node" -- bash -lc 'timedatectl show -p NTPSynchronized --value 2>/dev/null || echo unknown' | tr -d '\r')"

  if [[ -z "${node_epoch:-}" || ! "${node_epoch}" =~ ^[0-9]+$ ]]; then
    echo "${node}: ERROR: could not read time"
    continue
  fi

  delta_sec=$(( node_epoch - host_epoch ))
  abs_delta_sec="${delta_sec#-}"
  echo "${node}: ${node_iso} (epoch=${node_epoch}) delta=${delta_sec}s (abs=${abs_delta_sec}s) ntp=${ntp_sync}"
done
