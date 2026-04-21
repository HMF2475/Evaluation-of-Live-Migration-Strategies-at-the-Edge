#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash Network-live-migration/scripts/setup/check_node_exporter_metrics.sh [edge-node-1] [edge-node-2] [edge-host-1] ...

Validates that node_exporter is reachable and exposes a few key metrics.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -eq 0 ]]; then
  set -- edge-node-1 edge-node-2 edge-host-1
fi

for node in "$@"; do
  echo "=== metrics check: ${node} ==="
  multipass exec "$node" -- bash -lc '
    set -euo pipefail
    m="$(curl -fsS http://127.0.0.1:9100/metrics)"

    for k in \
      node_cpu_seconds_total \
      node_memory_MemAvailable_bytes \
      node_disk_read_bytes_total \
      node_disk_written_bytes_total \
      node_network_receive_bytes_total \
      node_network_transmit_bytes_total
    do
      grep -q "^${k}" <<<"$m" && echo "OK: ${k}" || { echo "MISSING: ${k}"; exit 1; }
    done
  '
done
