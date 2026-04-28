#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash WASM-migration/scripts/setup/install_wasm_node_deps.sh [--build-tools] [edge-node-1] [edge-node-2] ...

Installs runtime dependencies needed by the WASM migration runner inside
Multipass edge nodes. Also enables node_exporter for benchmark snapshots.

Options:
  --build-tools  Also install compilers, CMake, Rust/Cargo, and libcurl headers.

Default nodes: edge-node-1 edge-node-2
EOF
}

BUILD_TOOLS=0
NODES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --build-tools)
      BUILD_TOOLS=1
      shift
      ;;
    *)
      NODES+=("$1")
      shift
      ;;
  esac
done

if [[ ${#NODES[@]} -eq 0 ]]; then
  NODES=(edge-node-1 edge-node-2)
fi

for node in "${NODES[@]}"; do
  echo "=== WASM deps: ${node} ==="
  multipass exec "$node" -- bash -s -- "$BUILD_TOOLS" <<'REMOTE'
set -euo pipefail
BUILD_TOOLS="$1"
export DEBIAN_FRONTEND=noninteractive

runtime_packages=(
  ca-certificates
  curl
  tar
  gzip
  coreutils
  procps
  openssh-client
  openssh-server
  libcurl4
  prometheus-node-exporter
)

build_packages=(
  build-essential
  cmake
  pkg-config
  git
  libcurl4-openssl-dev
  cargo
  rustc
)

sudo apt-get update -qq
sudo apt-get install -y "${runtime_packages[@]}"

if [[ "$BUILD_TOOLS" == "1" ]]; then
  sudo apt-get install -y "${build_packages[@]}"
fi

sudo systemctl enable --now ssh
sudo systemctl enable --now prometheus-node-exporter

mkdir -p /home/ubuntu/wasm-migration/bin /home/ubuntu/wasm-migration/modules /home/ubuntu/wasm-migration/runs
chown -R ubuntu:ubuntu /home/ubuntu/wasm-migration

curl -fsS http://127.0.0.1:9100/metrics >/dev/null
echo "OK: runtime deps ready"
REMOTE
done
