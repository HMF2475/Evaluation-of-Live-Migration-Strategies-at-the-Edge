#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash Container/scripts/setup/install_node_exporter.sh [edge-node-1] [edge-node-2] ...

Installs and enables Prometheus node_exporter inside each Multipass VM.
Exposes metrics on: http://127.0.0.1:9100/metrics (inside the VM)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -eq 0 ]]; then
  set -- edge-node-1 edge-node-2
fi

for node in "$@"; do
  echo "=== node_exporter: ${node} ==="
  multipass exec "$node" -- bash -lc '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive

    if systemctl is-active --quiet prometheus-node-exporter 2>/dev/null; then
      echo "prometheus-node-exporter already active"
      exit 0
    fi

    sudo apt-get update -qq
    sudo apt-get install -y prometheus-node-exporter curl >/dev/null
    sudo systemctl enable --now prometheus-node-exporter

    sudo systemctl is-active --quiet prometheus-node-exporter
    curl -fsS http://127.0.0.1:9100/metrics >/dev/null
    echo "OK: node_exporter running on 9100"
  '
done

