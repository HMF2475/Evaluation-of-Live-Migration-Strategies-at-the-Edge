# Native CRIU Pre-Copy Live Migration Guide

## Overview

This guide implements **pre-copy live migration** of a native Linux process using CRIU's iterative memory copying technique.

**What it demonstrates:**
- Multiple pre-dump stages while process is **still running**
- Final dump after minimal pre-copy iterations
- Reduced process freeze time compared to cold migration
- Progressive memory snapshot transfer

**When to use pre-copy migration:**
- Minimizing downtime is critical
- Process memory is moderate 
- Iterative approach to convergence works for your workload
- Network is reliable (multiple transfers)

**Trade-offs:**
- ✅ Reduced freeze time (only final dump stops process)
- ✅ Process keeps running during pre-copy transfers
- ❌ More complex setup (multiple directories, symlinks)
- ❌ More transfers (each pre-dump + final dump)
- ❌ Requires careful directory structure management

---

## Prerequisites

- Two running Multipass nodes (e.g. `edge-node-1`, `edge-node-2`)
- Same CPU architecture on both nodes
- CRIU installed on both nodes
- Workload writes to `/home/ubuntu/counter.out` (this repo’s baseline scripts do)

---

## Quick Start (Automated)

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_counter_c.sh edge-node-1

python3 Container/scripts/orchestrators/criu_benchmark.py precopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode host \
  --iterations 2 \
  --run-id experimental-precopy-001
```

---

## Step 1: Start a Native Process on Source

**Use the C counter**  for consistent performance testing:

```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1
```

Optional (manual, without the helper script): compile `Container/scripts/counter.c` and redirect stdout to `/home/ubuntu/counter.out`:

```bash
multipass transfer Container/scripts/counter.c edge-node-1:/home/ubuntu/counter.c
multipass exec edge-node-1 -- bash -lc '
set -e
gcc -o /tmp/counter /home/ubuntu/counter.c
: > /home/ubuntu/counter.out
chmod 664 /home/ubuntu/counter.out
nohup /tmp/counter >> /home/ubuntu/counter.out 2>&1 &
echo $! > /home/ubuntu/counter.pid
cp /home/ubuntu/counter.pid /home/ubuntu/app.pid
sleep 2
tail -n 5 /home/ubuntu/counter.out
'
```

### Capture baseline
```bash
LAST_BEFORE=$(multipass exec edge-node-1 -- bash -lc "tail -n 1 /home/ubuntu/counter.out" | tr -d '\r')
echo "Last value before migration: ${LAST_BEFORE}"
```

---

## Step 2: First Pre-Dump (Process Running)

Perform the first pre-dump while the process is **still running**. This captures the initial memory state.

```bash
T_PREDUMP1_START=$(date +%s%N)

multipass exec edge-node-1 -- bash -lc '
set -e
SOURCE_PID=$(cat /home/ubuntu/counter.pid)

# Clean and prepare directories
sudo rm -rf /tmp/CRIU-predump-1 /tmp/CRIU-predump-2 /tmp/CRIU-final
sudo mkdir -p /tmp/CRIU-predump-1

# First pre-dump (process keeps running)
echo "Performing first pre-dump..."
sudo criu pre-dump \
  --tree "$SOURCE_PID" \
  -D /tmp/CRIU-predump-1 \
  -v4 \
  -o pre-dump-1.log \
  --shell-job

echo "First pre-dump complete. Process still running."
sudo ls -lh /tmp/CRIU-predump-1/
'

T_PREDUMP1_DONE=$(date +%s%N)
PREDUMP1_MS=$(( (T_PREDUMP1_DONE - T_PREDUMP1_START) / 1000000 ))
echo "Pre-dump 1 time: ${PREDUMP1_MS} ms"
```

**Key points:**
- `pre-dump` (not `dump`) keeps the process running
- Output goes to a directory (e.g., `/tmp/CRIU-predump-1`)
- Process continues writing to log while we capture state

---

## Step 3: Wait, then Second Pre-Dump

Wait a moment for more memory changes, then do another pre-dump to capture only the delta.

```bash
# Let the counter change more pages
sleep 5

T_PREDUMP2_START=$(date +%s%N)

multipass exec edge-node-1 -- bash -lc '
set -e
SOURCE_PID=$(cat /home/ubuntu/counter.pid)

# Prepare second pre-dump directory
sudo mkdir -p /tmp/CRIU-predump-2

# Second pre-dump, pointing to the first one as reference
echo "Performing second pre-dump (delta from first)..."
sudo criu pre-dump \
  --tree "$SOURCE_PID" \
  -D /tmp/CRIU-predump-2 \
  --prev-images-dir ../CRIU-predump-1 \
  -v4 \
  -o pre-dump-2.log \
  --shell-job

echo "Second pre-dump complete. Process still running."
sudo ls -lh /tmp/CRIU-predump-2/
'

T_PREDUMP2_DONE=$(date +%s%N)
PREDUMP2_MS=$(( (T_PREDUMP2_DONE - T_PREDUMP2_START) / 1000000 ))
echo "Pre-dump 2 time: ${PREDUMP2_MS} ms"
```

**Key points:**
- `--prev-images-dir` points to previous pre-dump output
- CRIU only captures **delta** (memory pages that changed since pre-dump 1)
- Process continues running during this capture

You can repeat this step multiple times if desired. Each iteration transfers smaller amounts of data.

---

## Step 4: Final Dump (Freeze Source)

Now perform the final `dump` (with `--track-mem` for proper delta tracking). This **freezes** the process.

```bash
T_DUMP_START=$(date +%s%N)

multipass exec edge-node-1 -- bash -lc '
set -e
SOURCE_PID=$(cat /home/ubuntu/counter.pid)

# Prepare final dump directory
sudo mkdir -p /tmp/CRIU-final

# Final dump (freezes process, captures only changes since last pre-dump)
echo "Performing final dump (process will freeze)..."
sudo criu dump \
  --tree "$SOURCE_PID" \
  -D /tmp/CRIU-final \
  --prev-images-dir ../CRIU-predump-2 \
  --track-mem \
  -v4 \
  -o dump.log \
  --shell-job \
  --leave-stopped

echo "Final dump complete. Process is now frozen."
sudo ls -lh /tmp/CRIU-final/
'

T_DUMP_DONE=$(date +%s%N)
DUMP_MS=$(( (T_DUMP_DONE - T_DUMP_START) / 1000000 ))
echo "Final dump time: ${DUMP_MS} ms"

FROZEN_LAST=$(multipass exec edge-node-1 -- bash -lc "tail -n 1 /home/ubuntu/counter.out" | tr -d '\r')
EXPECTED_AFTER=$((FROZEN_LAST + 1))
echo "Frozen last counter value: ${FROZEN_LAST}"
echo "Expected first value after restore: ${EXPECTED_AFTER}"
```

**Key points:**
- `--prev-images-dir` references the last pre-dump
- `--track-mem` enables memory tracking for proper delta
- `--leave-stopped` freezes the process (final dump only)
- This is the **last** image directory; prepare all 3 directories for transfer

---

## Step 5: Calculate Total Freeze Time

The total process freeze time is **only** the final dump phase (not pre-dumps):

```bash
TOTAL_FREEZE_MS=$DUMP_MS
echo "Process freeze time: ${TOTAL_FREEZE_MS} ms (only final dump)"
echo "Pre-copy overhead: ~$(( PREDUMP1_MS + PREDUMP2_MS )) ms (process was running)"
```

This is much better than cold migration, where freeze time = entire dump operation.

---

## Step 6: Archive All Image Directories

Bundle all three pre-dump and final dump directories for transfer.

```bash
multipass exec edge-node-1 -- bash -lc '
set -e

# Create a parent directory with all dumps
sudo mkdir -p /tmp/CRIU-all-dumps
sudo cp -r /tmp/CRIU-predump-1 /tmp/CRIU-all-dumps/
sudo cp -r /tmp/CRIU-predump-2 /tmp/CRIU-all-dumps/
sudo cp -r /tmp/CRIU-final /tmp/CRIU-all-dumps/

# Create archive
sudo tar -C /tmp -czf /tmp/CRIU-all-dumps.tar.gz CRIU-all-dumps

# Copy to user-accessible location
sudo cp /tmp/CRIU-all-dumps.tar.gz /home/ubuntu/CRIU-all-dumps.tar.gz
sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-all-dumps.tar.gz

echo "Archive created with all pre-dumps and final dump:"
ls -lh /home/ubuntu/CRIU-all-dumps.tar.gz
echo "Size in bytes:"
stat -c %s /home/ubuntu/CRIU-all-dumps.tar.gz
'
```

---

## Step 7: Transfer Archives to Destination

The archive must be transferred from source to destination. There are two methods:

### **Method A: Host-Mediated Transfer (via your machine)**

This routes the archive through your host machine - useful when direct SSH access is restricted.

```bash
HOST_ARCHIVE="$PWD/CRIU-all-dumps.tar.gz"
rm -f "$HOST_ARCHIVE"

echo "=== HOST-MEDIATED TRANSFER ==="
echo "Transferring pre-copy images to destination..."
T_TRANSFER_START=$(date +%s%N)

multipass transfer edge-node-1:/home/ubuntu/CRIU-all-dumps.tar.gz "$HOST_ARCHIVE"
multipass transfer "$HOST_ARCHIVE" edge-node-2:/home/ubuntu/CRIU-all-dumps.tar.gz

T_TRANSFER_DONE=$(date +%s%N)
TRANSFER_MS=$(( (T_TRANSFER_DONE - T_TRANSFER_START) / 1000000 ))

echo "=== Transfer Complete ==="
ls -lh "$HOST_ARCHIVE"
echo "Transfer time (via host): ${TRANSFER_MS} ms"

# Record the transfer method for metrics
TRANSFER_METHOD="host"
```

**Advantages:**
- ✅ Works with restricted network policies
- ✅ Easier to debug (files visible on host)
- ✅ Can pause/resume if needed

**Disadvantages:**
- ❌ Slower (two hops: source → host → destination)
- ❌ Requires local disk space for large archives

---

### **Method B: Direct Transfer (SSH between nodes)**

This transfers the archive directly between nodes - faster but requires SSH access between them.

If `scp` hangs or asks for a password, you need passwordless SSH trust `edge-node-1 → edge-node-2`. The orchestrator sets this up automatically for `--transfer-mode direct`; for the manual steps, follow the “First-time only: set up SSH trust” snippet in `Container/CRIU-COLD-MIGRATION.md`.

```bash
echo "=== DIRECT TRANSFER ==="

# Get destination node's SSH address
DEST_IP=$(multipass info edge-node-2 | grep IPv4 | awk '{print $2}')

# Transfer directly from source to destination
echo "Transferring directly from edge-node-1 to edge-node-2..."
T_TRANSFER_START=$(date +%s%N)

multipass exec edge-node-1 -- bash -lc "
  scp -o BatchMode=yes -o ConnectTimeout=10 \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      /home/ubuntu/CRIU-all-dumps.tar.gz \
      ubuntu@${DEST_IP}:/home/ubuntu/CRIU-all-dumps.tar.gz
"

T_TRANSFER_DONE=$(date +%s%N)
TRANSFER_MS=$(( (T_TRANSFER_DONE - T_TRANSFER_START) / 1000000 ))

echo "=== Direct Transfer Complete ==="
echo "Transfer time (direct SCP): ${TRANSFER_MS} ms"

# Record the transfer method for metrics
TRANSFER_METHOD="direct"
```

**Advantages:**
- ✅ Much faster (single hop)
- ✅ Doesn't use local bandwidth
- ✅ Better for remote deployments

**Disadvantages:**
- ❌ Requires SSH key setup between nodes
- ❌ May fail with firewalls/NAT

---

### **Which Method to Use?**

- **For benchmarking**: Choose based on your test scenario:
  - **Host-mediated**: Simulates cases where nodes can't communicate directly
  - **Direct**: Realistic cloud/edge deployment scenario
- **Use the `--transfer-mode` flag** when running the automated orchestrator:
  ```bash
  # Automated script handles all of this:
  python3 Container/scripts/orchestrators/criu_benchmark.py precopy \
    --source edge-node-1 \
    --dest edge-node-2 \
    --transfer-mode host    # or 'direct'
  ```

---

## Step 8: Unpack on Destination

Extract the archive and verify directory structure.

```bash
multipass exec edge-node-2 -- bash -lc '
set -e

# Extract archive
sudo rm -rf /tmp/CRIU-all-dumps
sudo mkdir -p /tmp/CRIU-all-dumps
sudo tar -C /tmp -xzf /home/ubuntu/CRIU-all-dumps.tar.gz

# Verify structure
echo "=== Extracted directory structure ==="
sudo ls -lhR /tmp/CRIU-all-dumps/ | head -40

# For restore, we need final dump in /tmp/CRIU-final
sudo cp -r /tmp/CRIU-all-dumps/CRIU-final /tmp/CRIU-final
echo ""
echo "Files ready for restore in /tmp/CRIU-final"
sudo ls -lh /tmp/CRIU-final/
'
```

---

## Step 9: Prepare Restore Environment

```bash
multipass exec edge-node-2 -- bash -lc '
set -e
touch /home/ubuntu/counter.out
chmod 664 /home/ubuntu/counter.out
'
```

---

## Step 10: Restore with Pre-Copy Images

Restore the process from the final dump, with references to earlier pre-dumps (if CRIU needs them).

```bash
T_RESTORE_START=$(date +%s%N)

multipass exec edge-node-2 -- bash -lc '
set -e

# Restore from final dump
echo "Restoring process with pre-copy images..."
sudo criu restore \
  -D /tmp/CRIU-final \
  -v4 \
  -o restore.log \
  --shell-job \
  --restore-detached \
  --pidfile /tmp/CRIU-final/restored.pid

sleep 1
RESTORED_PID=$(sudo cat /tmp/CRIU-final/restored.pid)
echo "Restored PID: $RESTORED_PID"
ps -p "$RESTORED_PID" -o pid,cmd || true
'

T_RESTORE_DONE=$(date +%s%N)
RESTORE_MS=$(( (T_RESTORE_DONE - T_RESTORE_START) / 1000000 ))
echo "Restore time: ${RESTORE_MS} ms"
```

---

## Step 11: Verify Migration

```bash
sleep 3

OBSERVED_AFTER=$(multipass exec edge-node-2 -- bash -lc "tail -n 1 /home/ubuntu/counter.out" | tr -d '\r')

echo "=== Pre-Copy Migration Verification ==="
echo "Expected first value: ${EXPECTED_AFTER}"
echo "Observed: ${OBSERVED_AFTER}"

if [[ "$OBSERVED_AFTER" -ge "$EXPECTED_AFTER" ]]; then
  echo "✓ SUCCESS: Pre-copy migration worked!"
else
  echo "✗ FAILED: Counter mismatch"
fi

echo ""
echo "=== Recent counter values ==="
multipass exec edge-node-2 -- bash -lc "tail -n 15 /home/ubuntu/counter.out"
```

---

## Step 12: Cleanup

```bash
for n in edge-node-1 edge-node-2; do
  multipass exec "$n" -- bash -lc '
    sudo pkill -f "/tmp/counter" 2>/dev/null || true
    rm -f /home/ubuntu/counter.pid /home/ubuntu/app.pid \
          /home/ubuntu/counter.c /home/ubuntu/counter.out \
          /home/ubuntu/CRIU-counter.tar.gz
    sudo rm -rf /tmp/CRIU-*
  '
done

rm -f "$PWD/CRIU-all-dumps.tar.gz"
```

---


## References

- [CRIU Pre-Copy Live Migration](https://criu.org/Iterative_migration)
- [CRIU Images and Paths](https://criu.org/Images)
