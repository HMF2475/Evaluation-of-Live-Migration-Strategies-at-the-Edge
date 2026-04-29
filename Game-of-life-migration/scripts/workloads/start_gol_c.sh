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
# Correct path to gol.c in simplest-example
COUNTER_C="$SCRIPT_DIR/../../simplest-example/gol.c"
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

multipass exec "$NODE" -- bash -lc "
set -e
: > /home/ubuntu/gol.out
chmod 664 /home/ubuntu/gol.out
nohup /tmp/gol >> /home/ubuntu/gol.out 2>&1 &
PID=\$!
echo \$PID > /home/ubuntu/gol.pid
cp /home/ubuntu/gol.pid /home/ubuntu/app.pid
sleep 1
ps -p \$PID >/dev/null 2>&1
"

echo "[$(date +'%H:%M:%S')] ✓ Gol started"
