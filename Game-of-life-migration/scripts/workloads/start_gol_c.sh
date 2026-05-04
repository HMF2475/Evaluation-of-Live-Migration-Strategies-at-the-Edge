#!/usr/bin/env bash
#
# Compile and start a C-based gol application on the source node.
# This is the recommended baseline for CRIU migration (simpler than shell script).
#
# Usage: bash Game-of-life-migration/scripts/workloads/start_gol_c.sh [node-name]
# Example: bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1

set -euo pipefail

NODE="${1:-edge-node-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOL_WIDTH="${GOL_WIDTH:-2048}"
GOL_HEIGHT="${GOL_HEIGHT:-2048}"
GOL_SEED="${GOL_SEED:-0xC0FFEE}"
GOL_OUTPUT_MODE="${GOL_OUTPUT_MODE:-summary}"
GOL_PATTERN="${GOL_PATTERN:-random}"
COUNTER_C="$SCRIPT_DIR/gol.c"
GOL_SHA="$(sha256sum "$COUNTER_C" | awk '{print $1}')"

echo "[$(date +'%H:%M:%S')] Ensuring gol binary on $NODE..."

if ! multipass exec "$NODE" -- bash -lc "test -x /tmp/gol && test -f /tmp/gol.source.sha256 && grep -qx '$GOL_SHA' /tmp/gol.source.sha256" >/dev/null 2>&1; then
  echo "[$(date +'%H:%M:%S')] Transferring gol.c to $NODE..."

  multipass transfer "$COUNTER_C" "$NODE:/home/ubuntu/gol.c"

  echo "[$(date +'%H:%M:%S')] Compiling gol.c on $NODE..."

  multipass exec "$NODE" -- bash -lc "
  set -e
  gcc -o /tmp/gol /home/ubuntu/gol.c
  chmod +x /tmp/gol
  echo '$GOL_SHA' > /tmp/gol.source.sha256
  "
else
  echo "[$(date +'%H:%M:%S')] Reusing existing /tmp/gol on $NODE"
fi

echo "[$(date +'%H:%M:%S')] Starting gol on $NODE..."
echo "[$(date +'%H:%M:%S')] Grid: ${GOL_WIDTH}x${GOL_HEIGHT} (two uint32 grids, approx $((GOL_WIDTH * GOL_HEIGHT * 8 / 1024 / 1024)) MiB heap)"
echo "[$(date +'%H:%M:%S')] Output mode: ${GOL_OUTPUT_MODE}"
echo "[$(date +'%H:%M:%S')] Pattern: ${GOL_PATTERN}"

multipass exec "$NODE" -- bash -lc "
set -e
: > /home/ubuntu/gol.out
chmod 664 /home/ubuntu/gol.out
GOL_WIDTH='$GOL_WIDTH' GOL_HEIGHT='$GOL_HEIGHT' GOL_SEED='$GOL_SEED' GOL_OUTPUT_MODE='$GOL_OUTPUT_MODE' GOL_PATTERN='$GOL_PATTERN' nohup /tmp/gol >> /home/ubuntu/gol.out 2>&1 &
PID=\$!
echo \$PID > /home/ubuntu/gol.pid
cp /home/ubuntu/gol.pid /home/ubuntu/app.pid
sleep 1
ps -p \$PID >/dev/null 2>&1
"

echo "[$(date +'%H:%M:%S')] ✓ Gol started"
