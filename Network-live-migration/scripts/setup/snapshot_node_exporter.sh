#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash Network-live-migration/scripts/setup/snapshot_node_exporter.sh --node edge-node-1 --out /tmp/edge-node-1.prom

Fetches node_exporter metrics from inside the VM (localhost:9100) and stores
them on the host at the given output path.
EOF
}

NODE=""
OUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node) NODE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -z "${NODE}" || -z "${OUT}" ]] && { usage; exit 1; }

mkdir -p "$(dirname "$OUT")"
multipass exec "$NODE" -- bash -lc 'curl -fsS http://127.0.0.1:9100/metrics' >"$OUT"
echo "Saved: ${OUT}"
