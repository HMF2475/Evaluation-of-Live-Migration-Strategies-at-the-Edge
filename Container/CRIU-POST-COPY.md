# Native CRIU Post-Copy Live Migration Guide (TODO)

## Overview

This guide implements **post-copy (lazy) live migration** using CRIU's `lazy-pages` mechanism.

**What it demonstrates:**
- Minimal initial dump (only metadata and page maps)
- Rapid process start on destination
- On-demand page fetching from source while process runs
- Lowest possible freeze time

**When to use post-copy migration:**
- Ultra-low downtime is critical (milliseconds matter)
- Processes with large working sets
- Source node will remain available during initial restore phase
- Network is **stable** (page faults require source connectivity)

**Trade-offs:**
- ✅ Minimal freeze time 
- ✅ Process starts immediately on destination
- ❌ Most complex setup (requires page server)
- ❌ Risky: depends on source availability during restore
- ❌ Network latency becomes app latency initially

---

## Prerequisites

Same as pre-copy, plus:
- Stable network between source and destination
- Source node must stay running until restore completes
- CRIU must be compiled with lazy-pages support
- Process should have moderate memory footprint for demo

### Verify lazy-pages support
```bash
multipass exec edge-node-1 -- bash -c 'criu --help | grep -i lazy'
# Should show lazy-pages related options
```

---

## Step 1: Start Native Process

```bash
multipass exec edge-node-1 -- bash -lc '
cat > /home/ubuntu/counter.sh << "EOF"
#!/usr/bin/env bash
i=0
while true; do
  echo "$i" >> /home/ubuntu/counter.log
  i=$((i+1))
  sleep 1
done
EOF

chmod +x /home/ubuntu/counter.sh
nohup /home/ubuntu/counter.sh >/home/ubuntu/counter.out 2>&1 &
echo $! > /home/ubuntu/counter.pid
sleep 3

SOURCE_PID=$(cat /home/ubuntu/counter.pid)
ps -p "$SOURCE_PID" -o pid,cmd
tail -n 5 /home/ubuntu/counter.log
'
```

### Capture baseline
```bash
LAST_BEFORE=$(multipass exec edge-node-1 -- bash -lc "tail -n 1 /home/ubuntu/counter.log" | tr -d '\r')
echo "Last value before migration: ${LAST_BEFORE}"
EXPECTED_AFTER=$((LAST_BEFORE + 1))
```

---

## Step 2: Dump with Lazy-Pages Flag

Perform dump with `--lazy-pages` flag. This creates minimal images (no full memory dump).

```bash
T_DUMP_START=$(date +%s%N)

# Get source node IP for later
SOURCE_IP=$(multipass list | grep edge-node-1 | awk '{print $3}')
echo "Source IP: $SOURCE_IP"
SOURCE_PORT=9999

multipass exec edge-node-1 -- bash -lc '
set -e
SOURCE_PID=$(cat /home/ubuntu/counter.pid)

sudo rm -rf /tmp/CRIU-lazy
sudo mkdir -p /tmp/CRIU-lazy

echo "Dumping with lazy-pages (minimal freeze)..."
sudo criu dump \
  --tree "$SOURCE_PID" \
  -D /tmp/CRIU-lazy \
  --lazy-pages \
  --address '"$SOURCE_IP"' \
  --port '"$SOURCE_PORT"' \
  -v4 \
  -o dump.log \
  --shell-job \
  --leave-stopped

echo "Dump complete. Process frozen, page server ready."
sudo ls -lh /tmp/CRIU-lazy/
'

T_DUMP_DONE=$(date +%s%N)
DUMP_MS=$(( (T_DUMP_DONE - T_DUMP_START) / 1000000 ))
echo "Dump time (minimal freeze): ${DUMP_MS} ms"
```

**Key flags:**
- `--lazy-pages`: Enable lazy page loading
- `--address`: IP address where page server will listen
- `--port`: Port for page server (must be accessible from destination)

---

## Step 3: Start Page Server on Source

The page server listens for page fault requests from the destination.

```bash
# Start page server in background
echo "Starting page server on source (${SOURCE_IP}:${SOURCE_PORT})..."
multipass exec edge-node-1 -- bash -lc '
  sudo criu page-server \
    -D /tmp/CRIU-lazy \
    --address '"$SOURCE_IP"' \
    --port '"$SOURCE_PORT"' \
    -v4 \
    -o page-server.log &
  
  echo "Page server started (PID: $!)"
  sleep 1
' &
PAGE_SERVER_PID=$!

# Give page server time to start
sleep 2
```

---

## Step 4: Archive Images

```bash
multipass exec edge-node-1 -- bash -lc '
set -e

# Archive the minimal dump
sudo tar -C /tmp -czf /tmp/CRIU-lazy.tar.gz CRIU-lazy
sudo cp /tmp/CRIU-lazy.tar.gz /home/ubuntu/CRIU-lazy.tar.gz
sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-lazy.tar.gz

echo "Archive created (minimal size):"
ls -lh /home/ubuntu/CRIU-lazy.tar.gz
'
```

---

## Step 5: Transfer Images to Destination

```bash
HOST_ARCHIVE="$PWD/CRIU-lazy.tar.gz"
rm -f "$HOST_ARCHIVE"

echo "Transferring lazy-migration images..."
T_TRANSFER_START=$(date +%s%N)

multipass transfer edge-node-1:/home/ubuntu/CRIU-lazy.tar.gz "$HOST_ARCHIVE"
multipass transfer "$HOST_ARCHIVE" edge-node-2:/home/ubuntu/CRIU-lazy.tar.gz

T_TRANSFER_DONE=$(date +%s%N)
TRANSFER_MS=$(( (T_TRANSFER_DONE - T_TRANSFER_START) / 1000000 ))

echo "Transfer time: ${TRANSFER_MS} ms"
echo "Archive size: $(ls -lh "$HOST_ARCHIVE" | awk '{print $5}')"
```

---

## Step 6: Unpack on Destination

```bash
multipass exec edge-node-2 -- bash -lc '
set -e

sudo rm -rf /tmp/CRIU-lazy
sudo mkdir -p /tmp/CRIU-lazy
sudo tar -C /tmp -xzf /home/ubuntu/CRIU-lazy.tar.gz

echo "Images extracted on destination:"
sudo ls -lh /tmp/CRIU-lazy/
'
```

---

## Step 7: Start Lazy-Pages Daemon on Destination

The lazy-pages daemon handles page faults and fetches pages from the source page server.

```bash
# Get destination IP
DEST_IP=$(multipass list | grep edge-node-2 | awk '{print $3}')
echo "Destination IP: $DEST_IP"

echo "Starting lazy-pages daemon on destination..."
multipass exec edge-node-2 -- bash -lc '
  cd /tmp/CRIU-lazy
  sudo criu lazy-pages \
    -D /tmp/CRIU-lazy \
    --page-server \
    --address '"$SOURCE_IP"' \
    --port '"$SOURCE_PORT"' \
    -v4 \
    -o lazy-pages.log &
  
  LAZY_PID=$!
  echo "Lazy-pages daemon started (PID: $LAZY_PID)"
  sleep 1
' &
LAZY_DAEMON_PID=$!

sleep 2
```

---

## Step 8: Prepare Restore Environment

```bash
multipass exec edge-node-2 -- bash -lc '
touch /home/ubuntu/counter.log /home/ubuntu/counter.out
chmod 664 /home/ubuntu/counter.log /home/ubuntu/counter.out
'
```

---

## Step 9: Restore with Lazy-Pages

Restore the process. It will start immediately but fetch pages on-demand.

```bash
T_RESTORE_START=$(date +%s%N)

multipass exec edge-node-2 -- bash -lc '
  cd /tmp/CRIU-lazy
  
  echo "Restoring process with lazy-pages..."
  sudo criu restore \
    -D /tmp/CRIU-lazy \
    --lazy-pages \
    -v4 \
    -o restore.log \
    --shell-job \
    --restore-detached \
    --pidfile /tmp/CRIU-lazy/restored.pid
  
  sleep 1
  RESTORED_PID=$(sudo cat /tmp/CRIU-lazy/restored.pid)
  echo "Restored PID: $RESTORED_PID"
'

T_RESTORE_DONE=$(date +%s%N)
RESTORE_MS=$(( (T_RESTORE_DONE - T_RESTORE_START) / 1000000 ))
echo "Restore time: ${RESTORE_MS} ms"
```

**Key flag:**
- `--lazy-pages`: Enable lazy page loading during restore

---

## Step 10: Monitor Page Transfer

While the process runs, pages are fetched from source on-demand.

```bash
echo "Process is now running on destination with lazy page fetching..."
echo "Monitoring for 10 seconds..."

for i in {1..10}; do
  multipass exec edge-node-2 -- bash -lc "
    RESTORED_PID=\$(sudo cat /tmp/CRIU-lazy/restored.pid)
    if ps -p \$RESTORED_PID >/dev/null 2>&1; then
      echo \"[$i] Process running, page faults ongoing\"
    else
      echo \"[$i] Process exited\"
      break
    fi
  "
  sleep 1
done
```

---

## Step 11: Verify Migration

```bash
sleep 3

OBSERVED_AFTER=$(multipass exec edge-node-2 -- bash -lc "tail -n 1 /home/ubuntu/counter.log" | tr -d '\r')

echo "=== Post-Copy Migration Verification ==="
echo "Expected first value: ${EXPECTED_AFTER}"
echo "Observed: ${OBSERVED_AFTER}"

if [[ "$OBSERVED_AFTER" -ge "$EXPECTED_AFTER" ]]; then
  echo "✓ SUCCESS: Post-copy migration worked!"
  echo "Process is running on destination with on-demand page fetching"
else
  echo "✗ FAILED: Counter mismatch"
fi

echo ""
echo "=== Counter values (some may be missing due to page faults) ==="
multipass exec edge-node-2 -- bash -lc "tail -n 20 /home/ubuntu/counter.log"
```

---

## Step 12: Stop Page Server

Once migration is stable, stop the page server on source.

```bash
# Stop page server
multipass exec edge-node-1 -- bash -lc '
  sudo pkill -f "criu page-server" || true
  echo "Page server stopped"
'
```

**Warning:** Stopping the page server while process is still fetching pages will cause page fault failures!

---

## Step 13: Cleanup

```bash
for n in edge-node-1 edge-node-2; do
  multipass exec "$n" -- bash -lc '
    sudo pkill -f counter.sh 2>/dev/null || true
    sudo pkill -f "criu page-server" 2>/dev/null || true
    sudo pkill -f "criu lazy-pages" 2>/dev/null || true
    rm -f /home/ubuntu/counter.* /home/ubuntu/CRIU-*
    sudo rm -rf /tmp/CRIU-*
  '
done

rm -f "$PWD/CRIU-lazy.tar.gz"
```

---

## References

- [CRIU Post-Copy Live Migration (Lazy-Pages)](https://criu.org/Lazy_migration)
- [CRIU Page-Server](https://criu.org/Page_server)

