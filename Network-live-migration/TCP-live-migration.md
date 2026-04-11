# TCP Live Migration (CRIU) — Three Nodes

This guide documents the full TCP client migration workflow used in this repository.

The workload is a TCP client process migrated between nodes while keeping an established socket to a fixed server.

## Topology

- Server node: `edge-host-1`
- Source client node: `edge-node-1`
- Destination client node: `edge-node-2`

Supported migration methods:

- `cold`
- `precopy`
- `postcopy` (lazy-pages)

Supported transfer modes:

- `host`: source -> relay -> destination
- `direct`: source -> destination via SCP/SSH

Metrics are appended to `Network-live-migration/metrics/migration_metrics.csv` using the same 16-column schema as `Container/metrics/migration_metrics.csv`.

## Prerequisites

- Multipass VMs exist and are reachable:
  - `edge-node-1`
  - `edge-node-2`
  - `edge-host-1`
- `node-bootstrap` finished on each VM.
- CRIU available on all required nodes.
- `sudo` works non-interactively inside the VMs.
- Optional but recommended:
  - node_exporter installed on all three nodes for CPU/memory/disk plots.

Quick readiness check:

```bash
for n in edge-node-1 edge-node-2 edge-host-1; do
  echo "=== $n ==="
  multipass exec "$n" -- bash -lc '
    systemctl is-active node-bootstrap || true
    criu --version 2>/dev/null | head -1 || true
    podman --version 2>/dev/null | head -1 || true
  '
done
```

## Networking Requirement

Use a single NIC per VM for this TCP workflow.

Important:

- Do not add a second interface on the same subnet (for example by attaching an extra `networks { ... }` block to `mpqemubr0`), because that creates route/ARP ambiguity that can break VIP handoff.
- If you changed Terraform networking and see dual interfaces (`ens3` + `ens4`) in the same subnet, recreate instances with the corrected Terraform configuration.

Quick check:

```bash
for n in edge-node-1 edge-node-2 edge-host-1; do
  echo "=== $n ==="
  multipass exec "$n" -- bash -lc 'ip -br -4 addr; ip route | grep default'
done
```

## Why VIP Is Required

CRIU can restore established TCP sockets using TCP repair mode, but cross-host restore requires preserving the same local client IP used by that socket.

This workflow uses a VIP (default `10.22.132.250`) and moves it from source to destination between dump and restore.

Without this VIP handoff, the restored socket state does not match TCP tuple expectations and continuity fails.

## One Manual Run

Set a port and VIP:

```bash
export TCP_PORT=5000
export TCP_VIP=10.22.132.250
```

1. Reset nodes

```bash
python3 Network-live-migration/scripts/setup/reset_nodes.py edge-node-1 edge-node-2 edge-host-1 "$TCP_VIP"
```

2. Start server

```bash
bash Network-live-migration/scripts/workloads/start_tcp_server.sh edge-host-1 "$TCP_PORT"
```

3. Start client on source (binds to VIP)

```bash
TCP_VIP="$TCP_VIP" bash Network-live-migration/scripts/workloads/start_tcp_client.sh edge-node-1 edge-host-1 "$TCP_PORT"
```

4. Run one migration (example: cold, host mode)

```bash
python3 Network-live-migration/scripts/orchestrators/tcp_client_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --server edge-host-1 \
  --transfer-mode host \
  --relay-node edge-host-1
```

Alternative single-run examples:

```bash
python3 Network-live-migration/scripts/orchestrators/tcp_client_benchmark.py precopy \
  --source edge-node-1 --dest edge-node-2 --server edge-host-1 \
  --transfer-mode host --relay-node edge-host-1 \
  --iterations 2

python3 Network-live-migration/scripts/orchestrators/tcp_client_benchmark.py postcopy \
  --source edge-node-1 --dest edge-node-2 --server edge-host-1 \
  --transfer-mode host --relay-node edge-host-1 \
  --page-server-port 9999
```

For direct transfer, set `--transfer-mode direct` (relay not required).

## Repeat Suite (Recommended)

Run all methods in both transfer modes and collect node_exporter snapshots:

```bash
python3 Network-live-migration/scripts/orchestrators/repeat_tcp_client_benchmarks.py \
  --source edge-node-1 \
  --dest edge-node-2 \
  --server edge-host-1 \
  --relay-node edge-host-1 \
  --port 5000 \
  --vip 10.22.132.250 \
  --strategies cold precopy postcopy \
  --iterations 2 \
  --host-runs 10 \
  --direct-runs 10 \
  --snapshot-node-metrics
```

Disable plots during the suite:

```bash
python3 Network-live-migration/scripts/orchestrators/repeat_tcp_client_benchmarks.py ... --no-plots
```

Useful optional flag:

- `--continue-on-failure`: continue remaining runs even if one run fails.

## Plot Generation

The repeat runner auto-generates plots unless `--no-plots` is used.

Manual plot generation for one suite:

```bash
python3 Network-live-migration/scripts/visualization/generate_all_plots.py \
  --csv Network-live-migration/metrics/migration_metrics.csv \
  --run-ids-file Network-live-migration/metrics/run_logs/<suite>.run_ids.txt \
  --out-dir Network-live-migration/metrics/plots/<suite> \
  --node-metrics-dir Network-live-migration/metrics/node_exporter
```

Expected output files:

- `downtime_comparison.png`
- `phase_breakdown.png`
- `transfer_analysis.png`
- `node_exporter_summary.png` (when snapshots exist)

## Validation Checklist (Per Run)

A run is considered healthy when:

- Migration command exits with success.
- Client output continues after restore (for example `PP N -> N`).
- Server-side connection count does not increase due to reconnect.
- CSV row exists with `success=True`.

Quick CSV check for one run id:

```bash
python3 - <<'PY'
import csv
run_id = "<run_id_here>"
with open("Network-live-migration/metrics/migration_metrics.csv", newline="") as f:
    for r in csv.DictReader(f):
        if r["run_id"] == run_id:
            print(r)
            break
PY
```

## Troubleshooting

### node-bootstrap still running or failed

```bash
for n in edge-node-1 edge-node-2 edge-host-1; do
  echo "=== $n ==="
  multipass exec "$n" -- bash -lc '
    systemctl status node-bootstrap --no-pager -n 40 || true
    sudo tail -n 80 /var/log/node-bootstrap.log || true
  '
done
```

### Client cannot connect during startup

- Verify server is listening on the expected port.
- Verify single-NIC topology (no duplicate interfaces on same subnet).
- Verify VIP is present on source before migration.

Checks:

```bash
multipass exec edge-host-1 -- ss -ltn | grep ':5000 '
multipass exec edge-node-1 -- ip -4 addr
multipass exec edge-node-1 -- ss -tn state established
```

### Restore fails with TCP/socket errors

- Confirm VIP moved to destination.
- Confirm source no longer holds VIP.
- Confirm ARP/neighbor refresh occurred (the scripts send gratuitous ARP, but races are still possible).

Checks:

```bash
multipass exec edge-node-1 -- ip -4 addr
multipass exec edge-node-2 -- ip -4 addr
multipass exec edge-host-1 -- ip neigh | grep 10.22.132.250 || true
```

### Postcopy fails with address-in-use/page-server conflicts

- Cleanup stale CRIU processes and retry.
- Change `--page-server-port` if needed.

```bash
python3 Network-live-migration/scripts/setup/reset_nodes.py edge-node-1 edge-node-2 edge-host-1 10.22.132.250
python3 Network-live-migration/scripts/orchestrators/tcp_client_benchmark.py postcopy ... --page-server-port 10099
```

### Useful diagnostics

```bash
bash Network-live-migration/scripts/helpers/diagnose_migration.sh --source edge-node-1 --dest edge-node-2
```

## Runtime Artifacts on Nodes

Client (source/destination):

- `/home/ubuntu/tcp_client.pid` (legacy alias: `/home/ubuntu/client.pid`)
- `/home/ubuntu/tcp_client.out`
- `/home/ubuntu/tcp_vip.txt`
- `/home/ubuntu/tcp_server_endpoint.txt`

Server (`edge-host-1`):

- `/home/ubuntu/tcp_server.pid`
- `/home/ubuntu/tcp_server.out`

CRIU work directories:

- `/tmp/CRIU-tcp-client`
- `/tmp/CRIU-tcp-client.tar.gz`

## Related Docs

- `Network-live-migration/README.md`
- `Network-live-migration/CRIU-limitations.md`
- `Network-live-migration/metrics/README.md`
- `Network-live-migration/scripts/orchestrators/README.md`
