#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 2 ]; then
  cat <<'EOF'
Usage:
  bash Network-live-migration/scripts/setup/apply_tc_netem.sh <node> <interface> [--delay 80ms] [--loss 1%] [--rate 20mbit]
  bash Network-live-migration/scripts/setup/apply_tc_netem.sh <node> <interface> --clear
EOF
  exit 1
fi

NODE="$1"
IFACE="$2"
shift 2

CLEAR=false
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --clear) CLEAR=true ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done

if $CLEAR; then
  multipass exec "$NODE" -- bash -lc "sudo tc qdisc del dev $IFACE root 2>/dev/null || true"
  echo "[netem] cleared qdisc on $NODE:$IFACE"
  exit 0
fi

if [ ${#ARGS[@]} -eq 0 ]; then
  echo "ERROR: provide at least one netem parameter or use --clear"
  exit 1
fi

PARAMS="${ARGS[*]}"
multipass exec "$NODE" -- bash -lc "
  sudo tc qdisc del dev $IFACE root 2>/dev/null || true
  sudo tc qdisc add dev $IFACE root netem $PARAMS
  sudo tc qdisc show dev $IFACE
"
