# Native CRIU Cold Migration Guide

## Overview

This guide implements **cold migration** (freeze → dump → transfer → restore) of a process using CRIU directly between two Multipass nodes.

**What it demonstrates:**
- Direct CRIU dump/restore of a user process (not Podman-wrapped)
- Full state snapshot with process freeze
- Network transfer of CRIU image files
- Process continuity validation (counter resumes at correct value)

**When to use cold migration:**
- Baseline/reference implementation
- Processes where brief downtime is acceptable
- Guaranteed successful migration (all state transferred before restore)
- Network-only scenarios (sockets require special handling)

**Trade-offs:**
- ✅ Simplest to implement and debug
- ✅ Most reliable (all state transferred before restore)
- ❌ Process freeze during dump + transfer (longest downtime)
- ❌ Requires disk space for full memory dump

---


### Verify prerequisites
```bash
for n in edge-node-1 edge-node-2; do
  echo "=== $n ==="
  multipass exec "$n" -- bash -c '
    echo "Arch: $(uname -m)"
    echo "CRIU: $(criu --version | head -1)"
    echo "Home: $(ls -ld ~)"
  '
done
```

---

## Step 1: Start a Native Process on Source

**Use the C counter** for consistent performance testing:

```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1
```

Or **copy the source code manually** if running outside the repo:

```bash
multipass exec edge-node-1 -- bash -lc '
# Create C counter source
cat > /home/ubuntu/counter.c << "EOF"
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>

static volatile int running = 1;
static FILE *logfile = NULL;

void signal_handler(int sig) {
    running = 0;
}

int main(int argc, char *argv[]) {
    const char *logpath = "/home/ubuntu/counter.log";
    unsigned long counter = 0;

    if (argc > 1) {
        logpath = argv[1];
    }

    logfile = fopen(logpath, "w");
    if (!logfile) {
        perror("fopen");
        return 1;
    }

    if (dup2(fileno(logfile), STDOUT_FILENO) < 0) {
        perror("dup2");
        fclose(logfile);
        return 1;
    }

    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);

    while (running) {
        printf("%lu\n", counter);
        fflush(stdout);
        counter++;
        sleep(1);
    }

    fclose(logfile);
    return 0;
}
EOF

# Compile and run
cc /home/ubuntu/counter.c -o /home/ubuntu/counter
nohup /home/ubuntu/counter >/dev/null 2>&1 &
echo $! > /home/ubuntu/counter.pid

# Wait for log to populate
sleep 3

# Verify running
SOURCE_PID=$(cat /home/ubuntu/counter.pid)
ps -p "$SOURCE_PID" -o pid,cmd
echo "Recent log entries:"
tail -n 5 /home/ubuntu/counter.log
'
```

### Capture "before" state for verification
```bash
LAST_BEFORE=$(multipass exec edge-node-1 -- bash -lc "tail -n 1 /home/ubuntu/counter.log" | tr -d '\r')
echo "Last counter value before migration: ${LAST_BEFORE}"
EXPECTED_AFTER=$((LAST_BEFORE + 1))
echo "Expected first value after restore: ${EXPECTED_AFTER}"
```

---

## Step 2: Dump Process with CRIU (Freeze Source)

The `dump` command stops the process, saves its full state to image files, and **leaves it stopped** (unless `--leave-running` is used).

```bash
multipass exec edge-node-1 -- bash -lc '
set -e
SOURCE_PID=$(cat /home/ubuntu/counter.pid)

# Clean any previous dump
sudo rm -rf /tmp/CRIU-counter
sudo mkdir -p /tmp/CRIU-counter

# Perform the dump (freezes the process)
echo "Performing CRIU dump of PID $SOURCE_PID..."
sudo criu dump \
  --tree "$SOURCE_PID" \
  -D /tmp/CRIU-counter \
  -v4 \
  -o dump.log \
  --shell-job

# List dumped image files
echo "=== Dumped image files ==="
sudo ls -lh /tmp/CRIU-counter/
'
```

**What happened:**
- `--tree <pid>`: Dump the process and its children
- `-D <dir>`: Output directory for CRIU image files
- `-v4`: Verbose logging (for debugging)
- `-o dump.log`: Log file
- `--shell-job`: Required for shell scripts; tells CRIU to handle session/process group specially

After this step, the counter process is **frozen** on `edge-node-1`.

---

## Step 3: Archive Dump Images

Create a compressed archive of the CRIU images for transfer.

```bash
multipass exec edge-node-1 -- bash -lc '
set -e

# Create archive
sudo tar -C /tmp -czf /tmp/CRIU-counter.tar.gz CRIU-counter

# Copy to user-accessible location
sudo cp /tmp/CRIU-counter.tar.gz /home/ubuntu/CRIU-counter.tar.gz
sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-counter.tar.gz

# Show archive size
echo "=== Archive created ==="
ls -lh /home/ubuntu/CRIU-counter.tar.gz
echo "Size in bytes:"
stat -c %s /home/ubuntu/CRIU-counter.tar.gz
'
```

---

## Step 4: Transfer Archive to Destination

The archive must be transferred from source to destination. There are two methods:

### **Method A: Host-Mediated Transfer (via your machine)**

This routes the archive through your host machine - useful when direct SSH access is restricted.

```bash
# Create a working directory on the host
HOST_ARCHIVE="$PWD/CRIU-counter.tar.gz"
rm -f "$HOST_ARCHIVE"

echo "=== HOST-MEDIATED TRANSFER ==="
echo "Downloading from edge-node-1..."
T_TRANSFER_START=$(date +%s%N)

multipass transfer edge-node-1:/home/ubuntu/CRIU-counter.tar.gz "$HOST_ARCHIVE"

echo "Uploading to edge-node-2..."
multipass transfer "$HOST_ARCHIVE" edge-node-2:/home/ubuntu/CRIU-counter.tar.gz

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
- ❌ Requires local disk space

---

### **Method B: Direct Transfer (SSH between nodes)**

This transfers the archive directly between nodes - faster but requires SSH access between them.

```bash
echo "=== DIRECT TRANSFER ==="

# Get destination node's SSH address
DEST_IP=$(multipass info edge-node-2 | grep IPv4 | awk '{print $2}')

# Transfer directly from source to destination
echo "Transferring directly from edge-node-1 to edge-node-2..."
T_TRANSFER_START=$(date +%s%N)

multipass exec edge-node-1 -- bash -lc "
  scp -o StrictHostKeyChecking=no \
      /home/ubuntu/CRIU-counter.tar.gz \
      ubuntu@${DEST_IP}:/home/ubuntu/CRIU-counter.tar.gz
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
  python3 Container/scripts/orchestrators/criu_benchmark.py cold \
    --source edge-node-1 \
    --dest edge-node-2 \
    --transfer-mode host    # or 'direct'
  ```

---

## Step 5: Verify Architecture Compatibility

Before restoring, check that both nodes have compatible architectures for CRIU migration.

```bash
echo "=== Architecture Compatibility Check ==="
SOURCE_ARCH=$(multipass exec edge-node-1 -- uname -m)
DEST_ARCH=$(multipass exec edge-node-2 -- uname -m)

echo "Source architecture: $SOURCE_ARCH"
echo "Destination architecture: $DEST_ARCH"

if [ "$SOURCE_ARCH" = "$DEST_ARCH" ]; then
  echo "✓ Architectures match (same_arch=1)"
  SAME_ARCH=1
else
  echo "⚠ WARNING: Different architectures (same_arch=0)"
  echo "  Migration may fail if binaries are incompatible!"
  SAME_ARCH=0
fi

# These will be recorded in the metrics CSV
echo "Architecture metrics for results:"
echo "  src_arch: $SOURCE_ARCH"
echo "  dst_arch: $DEST_ARCH"
echo "  same_arch: $SAME_ARCH"
```

---

## Step 6: Unpack Archive on Destination

Extract the CRIU images on the destination node.

```bash
multipass exec edge-node-2 -- bash -lc '
set -e

# Extract archive
sudo rm -rf /tmp/CRIU-counter
sudo mkdir -p /tmp/CRIU-counter
sudo tar -C /tmp -xzf /home/ubuntu/CRIU-counter.tar.gz

# Verify extraction
echo "=== Extracted image files ==="
sudo ls -lh /tmp/CRIU-counter/
'
```

---

## Step 7: Prepare Restore Environment

Before restoring, the destination needs placeholder files for file descriptors that the process will write to.

```bash
multipass exec edge-node-2 -- bash -lc '
set -e

# Create empty log and output files with correct permissions
touch /home/ubuntu/counter.log /home/ubuntu/counter.out
chmod 664 /home/ubuntu/counter.log /home/ubuntu/counter.out

# Copy the script from source to destination (ensures inode compatibility)
echo "=== Files created for restore ==="
ls -l /home/ubuntu/counter.*
'
```

---

## Step 8: Restore Process on Destination

Restore the process from CRIU images on the destination node.

```bash
T_RESTORE_START=$(date +%s%N)

multipass exec edge-node-2 -- bash -lc '
set -e

# Restore the process
echo "Restoring process from CRIU images..."
sudo criu restore \
  -D /tmp/CRIU-counter \
  -v4 \
  -o restore.log \
  --shell-job \
  --restore-detached \
  --pidfile /tmp/CRIU-counter/restored.pid

# Print the restored PID
echo "Process restored. PID file: /tmp/CRIU-counter/restored.pid"
sleep 1
RESTORED_PID=$(sudo cat /tmp/CRIU-counter/restored.pid)
echo "Restored process PID: $RESTORED_PID"
'

T_RESTORE_DONE=$(date +%s%N)
RESTORE_MS=$(( (T_RESTORE_DONE - T_RESTORE_START) / 1000000 ))
echo "Restore time: ${RESTORE_MS} ms"
```

**Key flags:**
- `--restore-detached`: Start process and detach (don't wait for it to exit)
- `--pidfile`: Write PID to a file for later verification

---

## Step 9: Verify Migration Success

Check that the process is running and the counter continued from the correct value.

```bash
# Wait for process to start writing
sleep 3

OBSERVED_AFTER=$(multipass exec edge-node-2 -- bash -lc "tail -n 1 /home/ubuntu/counter.log" | tr -d '\r')

echo "=== Migration Verification ==="
echo "Expected first value: ${EXPECTED_AFTER}"
echo "Observed after restore: ${OBSERVED_AFTER}"

if [[ "$OBSERVED_AFTER" -ge "$EXPECTED_AFTER" ]]; then
  echo "✓ SUCCESS: Counter continued correctly"
else
  echo "✗ FAILED: Counter did not continue as expected"
fi

# Show recent log
echo ""
echo "=== Recent counter values (edge-node-2) ==="
multipass exec edge-node-2 -- bash -lc "tail -n 10 /home/ubuntu/counter.log"
```

---

## Step 10: Cleanup

Remove all migration artifacts.

```bash
# Clean up on both nodes
for n in edge-node-1 edge-node-2; do
  multipass exec "$n" -- bash -lc '
    sudo pkill -f counter.sh 2>/dev/null || true
    rm -f /home/ubuntu/counter.pid /home/ubuntu/counter.log \
          /home/ubuntu/counter.out /home/ubuntu/counter.sh \
          /home/ubuntu/CRIU-counter.tar.gz
    sudo rm -rf /tmp/CRIU-counter /tmp/CRIU-counter.tar.gz
  '
done

# Clean up on host
rm -f "$PWD/CRIU-counter.tar.gz"
```

---


## Troubleshooting

### Dump fails with "Can't dump" errors
```bash
# Check detailed dump log
multipass exec edge-node-1 -- sudo cat /tmp/CRIU-counter/dump.log
```

Common causes:
- Process uses unsupported features (e.g., certain network sockets, kernel modules)
- Insufficient capabilities or AppArmor restrictions
- Process tree complexity

### Restore fails
```bash
# Check restore log
multipass exec edge-node-2 -- sudo cat /tmp/CRIU-counter/restore.log
```

Common causes:
- Missing files that process expects to open (Step 6 preparation)
- File permission mismatches
- Incompatible architectures or kernel versions
- AppArmor/SELinux profile issues

### Process doesn't write to log after restore
- Ensure `/home/ubuntu/counter.log` exists with correct permissions
- Verify the restored process PID is correct
- Check logs: `multipass exec edge-node-2 -- bash -lc "cat /home/ubuntu/counter.out"`

---

## References

- [CRIU Official Documentation - Live Migration](https://criu.org/Live_migration)

