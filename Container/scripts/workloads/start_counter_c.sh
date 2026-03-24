#!/usr/bin/env bash
#
# Compile and start a C-based counter application on the source node.
# This is the recommended baseline for CRIU migration (simpler than shell script).
#
# Usage: bash Container/scripts/workloads/start_counter_c.sh [node-name]
# Example: bash Container/scripts/workloads/start_counter_c.sh edge-node-1

NODE="${1:-edge-node-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COUNTER_C="$SCRIPT_DIR/../counter.c"

echo "[$(date +'%H:%M:%S')] Transferring counter.c to $NODE..."

multipass transfer "$COUNTER_C" "$NODE:/home/ubuntu/counter.c"

echo "[$(date +'%H:%M:%S')] Compiling counter.c on $NODE..."

multipass exec "$NODE" -- bash -lc "
set -e
gcc -o /tmp/counter /home/ubuntu/counter.c
chmod +x /tmp/counter
"

echo "[$(date +'%H:%M:%S')] Starting counter on $NODE..."

multipass exec "$NODE" -- bash -lc "
nohup /tmp/counter /home/ubuntu/counter.log >/dev/null 2>&1 &
PID=\$!
echo \$PID > /home/ubuntu/counter.pid
cp /home/ubuntu/counter.pid /home/ubuntu/app.pid
sleep 1
ps -p \$PID >/dev/null 2>&1 && echo '✓ C counter running (PID: '\$PID')' || echo '✗ Counter failed'
"

echo "[$(date +'%H:%M:%S')] ✓ Counter started"
