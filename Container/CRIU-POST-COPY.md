# Native CRIU Post-Copy Live Migration Guide (Lazy-Pages)

## Overview

This guide implements **post-copy (lazy) live migration** using CRIU’s `--lazy-pages` feature.

Status in this repo:
- Implemented as a runnable strategy in `Container/scripts/orchestrators/postcopy_migration.py`.
- Usable via `python3 Container/scripts/orchestrators/criu_benchmark.py postcopy ...`.

**What it demonstrates:**
- Minimal initial dump (metadata + page maps only)
- Destination restores quickly and starts running
- Memory pages are fetched from the source **on demand** (page faults)

**Trade-offs:**
- ✅ Minimal freeze time on source
- ✅ Fast start on destination
- ❌ Requires stable VM-to-VM connectivity during the initial run on the destination
- ❌ If the source page-server dies early, the destination can page-fault and crash

---

## Prerequisites

Same as cold/pre-copy, plus:
- The source and destination must be able to reach each other directly over TCP (for the page-server).
- A free TCP port on the source (default in this repo examples: `9999`).
- CRIU lazy-pages support (requires `userfaultfd`).

### Verify lazy-pages support
```bash
multipass exec edge-node-1 -- bash -c 'criu --help | grep -i lazy'
# Should mention: --lazy-pages, and that it requires userfaultfd
```

---

## Quick Start (Automated)

`--transfer-mode` controls **only** how the CRIU image archive is transferred (`host` vs `direct`).  
Post-copy itself still requires direct VM-to-VM connectivity for the page-server.

Archive transfer and lazy-page traffic are different paths:

```text
Initial image archive:
  direct mode:                 source VM -> destination VM                 (scp)
  host mode, no relay:         source VM -> host machine -> destination VM (multipass transfer)
  host mode, with relay node:  source VM -> relay VM -> destination VM     (scp twice)

Lazy memory pages after restore:
  source page-server -> destination lazy-pages daemon -> restored process
```

So `--relay-node edge-host-1` can relay or cache the archive, but it does not relay post-copy page faults. The destination still needs TCP reachability to the source page-server.

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_counter_c.sh edge-node-1

python3 Container/scripts/orchestrators/criu_benchmark.py postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --page-server-port 9999 \
  --run-id postcopy-smoke-001
```

---

## Step 1: Start a Native Process on Source
```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1
```

## Step 2: Capture a “before” value (for verification)
```bash
LAST_BEFORE=$(multipass exec edge-node-1 -- bash -lc "tail -n 1 /home/ubuntu/counter.out" | tr -d '\r')
echo "Last value before dump: ${LAST_BEFORE}"
```

## Step 3: Start a lazy dump + page-server on the source

Pick a TCP port. If you previously aborted a run, that port may still be in use.

```bash
SOURCE_IP=$(multipass info edge-node-1 | grep IPv4 | awk '{print $2}')
PAGE_SERVER_PORT=9999

echo "Source IP: ${SOURCE_IP}"
echo "Page-server port: ${PAGE_SERVER_PORT}"

# Clean any previous artifacts and ensure no old listeners remain
multipass exec edge-node-1 -- bash -lc "
  set -e
  sudo pkill -9 -f '^criu (dump|page-server).*--lazy-pages' 2>/dev/null || true
  sudo rm -rf /tmp/CRIU-counter
  sudo mkdir -p /tmp/CRIU-counter
"

T_DUMP_START=$(date +%s%N)

echo "Starting CRIU dump with --lazy-pages (runs a page-server)..."
multipass exec edge-node-1 -- bash -lc "
  set -e
  SOURCE_PID=\$(cat /home/ubuntu/counter.pid)
  sudo nohup criu dump --tree \"\$SOURCE_PID\" -D /tmp/CRIU-counter \
    --lazy-pages --address 0.0.0.0 --port ${PAGE_SERVER_PORT} \
    -v4 -o dump.log --shell-job --leave-stopped \
    >/tmp/CRIU-counter/dump.stdout 2>&1 &
  echo \$! | sudo tee /tmp/CRIU-counter/dump.pid >/dev/null
  echo \"Dump/page-server PID: \$(sudo cat /tmp/CRIU-counter/dump.pid)\"
"

T_DUMP_DONE=$(date +%s%N)
DUMP_MS=$(( (T_DUMP_DONE - T_DUMP_START) / 1000000 ))
echo "Dump init time (approx freeze duration): ${DUMP_MS} ms"

echo "Checking that the page-server is listening on the source..."
multipass exec edge-node-1 -- bash -lc "sudo ss -lntp | grep -E \":${PAGE_SERVER_PORT}\\b\" || (echo 'NOT LISTENING' && exit 1)"

FROZEN_LAST=$(multipass exec edge-node-1 -- bash -lc "tail -n 1 /home/ubuntu/counter.out" | tr -d '\r')
EXPECTED_AFTER=$((FROZEN_LAST + 1))
echo "Frozen last value: ${FROZEN_LAST}"
echo "Expected first value after restore: ${EXPECTED_AFTER}"
```

This source-side CRIU dump process stays alive after the initial lazy dump. It listens on `${SOURCE_IP}:${PAGE_SERVER_PORT}` and serves memory pages that were not written into the initial image archive.

## Step 4: Archive the images on the source
```bash
multipass exec edge-node-1 -- bash -lc '
set -e
sudo tar -C /tmp -czf /tmp/CRIU-counter.tar.gz CRIU-counter
sudo cp /tmp/CRIU-counter.tar.gz /home/ubuntu/CRIU-counter.tar.gz
sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-counter.tar.gz
ls -lh /home/ubuntu/CRIU-counter.tar.gz
'
```

## Step 5: Transfer the archive to the destination (host vs direct)

This transfers only the small image archive; **memory pages are fetched later** from the source page-server.

#### Method A: Host-mediated transfer

Manual host-mediated transfer below uses your host machine and `multipass transfer` twice. The automated orchestrator has an extra form: if you pass `--transfer-mode host --relay-node edge-host-1`, it stages the archive through `edge-host-1` using `scp` from source -> relay and relay -> destination.

```bash
HOST_ARCHIVE="$PWD/CRIU-counter.tar.gz"
rm -f "$HOST_ARCHIVE"

T_TRANSFER_START=$(date +%s%N)
multipass transfer edge-node-1:/home/ubuntu/CRIU-counter.tar.gz "$HOST_ARCHIVE"
multipass transfer "$HOST_ARCHIVE" edge-node-2:/home/ubuntu/CRIU-counter.tar.gz
T_TRANSFER_DONE=$(date +%s%N)

TRANSFER_MS=$(( (T_TRANSFER_DONE - T_TRANSFER_START) / 1000000 ))
echo "Transfer time (host): ${TRANSFER_MS} ms"
```

#### Method B: Direct transfer (VM-to-VM SCP)

Direct transfer requires SSH trust `edge-node-1 → edge-node-2`. The orchestrator sets this up automatically for `--transfer-mode direct`; for the manual steps, follow the “First-time only: set up SSH trust” snippet in `Container/CRIU-COLD-MIGRATION.md`.

```bash
DEST_IP=$(multipass info edge-node-2 | grep IPv4 | awk '{print $2}')

T_TRANSFER_START=$(date +%s%N)
multipass exec edge-node-1 -- bash -lc "
  scp -o BatchMode=yes -o ConnectTimeout=10 \
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      /home/ubuntu/CRIU-counter.tar.gz \
      ubuntu@${DEST_IP}:/home/ubuntu/CRIU-counter.tar.gz
"
T_TRANSFER_DONE=$(date +%s%N)

TRANSFER_MS=$(( (T_TRANSFER_DONE - T_TRANSFER_START) / 1000000 ))
echo "Transfer time (direct): ${TRANSFER_MS} ms"
```

## Step 6: Unpack on the destination
```bash
multipass exec edge-node-2 -- bash -lc '
set -e
sudo rm -rf /tmp/CRIU-counter
sudo mkdir -p /tmp/CRIU-counter
sudo tar -C /tmp -xzf /home/ubuntu/CRIU-counter.tar.gz
sudo ls -lh /tmp/CRIU-counter/ | head
'
```

## Step 7: Prepare restore environment
```bash
multipass exec edge-node-2 -- bash -lc '
set -e
touch /home/ubuntu/counter.out
chmod 664 /home/ubuntu/counter.out
'
```

## Step 8: Start the lazy-pages daemon on the destination

This command runs on the destination. The `--page-server` flag name is confusing here: the destination-side `criu lazy-pages` process connects to the source page-server at `${SOURCE_IP}:${PAGE_SERVER_PORT}` and serves local page faults to the restored process.

```bash
multipass exec edge-node-2 -- bash -lc "
  set -e
  sudo nohup criu lazy-pages -D /tmp/CRIU-counter \
    --page-server --address ${SOURCE_IP} --port ${PAGE_SERVER_PORT} \
    -v4 -o lazy-pages.log \
    >/tmp/CRIU-counter/lazy-pages.stdout 2>&1 &
  echo \$! | sudo tee /tmp/CRIU-counter/lazy-pages.pid >/dev/null
  echo \"Lazy-pages PID: \$(sudo cat /tmp/CRIU-counter/lazy-pages.pid)\"
"
```

## Step 9: Restore on the destination with `--lazy-pages`
```bash
T_RESTORE_START=$(date +%s%N)

multipass exec edge-node-2 -- bash -lc '
set -e
sudo criu restore -D /tmp/CRIU-counter --lazy-pages \
  -v4 -o restore.log --shell-job --restore-detached \
  --pidfile /tmp/CRIU-counter/restored.pid
sleep 1
sudo cat /tmp/CRIU-counter/restored.pid
'

T_RESTORE_DONE=$(date +%s%N)
RESTORE_MS=$(( (T_RESTORE_DONE - T_RESTORE_START) / 1000000 ))
echo "Restore time: ${RESTORE_MS} ms"
```

## Step 10: Verify
```bash
sleep 3
OBSERVED_AFTER=$(multipass exec edge-node-2 -- bash -lc "tail -n 1 /home/ubuntu/counter.out" | tr -d '\r')

echo "=== Post-Copy Migration Verification ==="
echo "Expected first value (min): ${EXPECTED_AFTER}"
echo "Observed: ${OBSERVED_AFTER}"

if [[ "$OBSERVED_AFTER" -ge "$EXPECTED_AFTER" ]]; then
  echo "✓ SUCCESS: Counter continued correctly!"
else
  echo "✗ FAILED: Counter did not continue as expected"
fi
```

## Step 11: Stop the page-server and cleanup

Stop the source page-server only after the destination has fetched most/all pages.

```bash
multipass exec edge-node-1 -- bash -lc '
  sudo test -f /tmp/CRIU-counter/dump.pid && sudo kill -9 "$(cat /tmp/CRIU-counter/dump.pid)" 2>/dev/null || true
  sudo pkill -9 -f "^criu (dump|page-server).*--lazy-pages" 2>/dev/null || true
'

multipass exec edge-node-2 -- bash -lc '
  sudo test -f /tmp/CRIU-counter/lazy-pages.pid && sudo kill -9 "$(cat /tmp/CRIU-counter/lazy-pages.pid)" 2>/dev/null || true
  sudo pkill -9 -f "^criu lazy-pages" 2>/dev/null || true
'
```

---

## Notes & Troubleshooting

- `Observed` is usually **higher** than `Expected` because you typically wait a few seconds before reading the destination output.
- If you see `Can't bind page server: Address already in use`, pick a new `--port` and/or kill old CRIU listeners (`sudo ss -lntp | grep :PORT`).
- Post-copy uses `userfaultfd` internally (CRIU registers “lazy” VMAs at restore time; the `criu lazy-pages` daemon resolves page faults).
- A relay/mainframe can store and forward checkpoint archives, especially cold and pre-copy archives. For post-copy, the initial lazy archive is not enough by itself while pages are still lazy; another destination would still need access to the live source page-server or to a later complete checkpoint.

## References

- [CRIU Lazy migration](https://criu.org/Lazy_migration)
- [CRIU Page server](https://criu.org/Page_server)
