# Benchmark Guide (CRIU, Podman, WASM, Metrics, Plots)

This guide is the repo-level map for all migration experiments. It gives shared setup, one-command smoke runs, artifact locations, and links to deeper per-migration docs.

Use module-specific Markdown when you want exact manual flow, CRIU flags, or troubleshooting for one migration.

## 0) Experiment Map

| Benchmark | Folder | Technology | Workload | Strategies | Main doc |
|-----------|--------|------------|----------|------------|----------|
| Counter process | `Container/` | Native CRIU | C counter | `cold`, `precopy`, `postcopy` | `Container/README.md` |
| Podman container | `Container/` | Podman + CRIU | Counter container | cold checkpoint/restore baseline | `Container/PODMAN-MIGRATION.md` |
| Game of Life process | `Game-of-life-migration/` | Native CRIU | Conway's Game of Life | `cold`, `precopy`, `postcopy` | `Game-of-life-migration/README.md` |
| TCP client process | `Network-live-migration/` | Native CRIU | TCP client with established socket | `cold`, `precopy`, `postcopy` | `Network-live-migration/TCP-live-migration.md` |
| WebAssembly | `WASM-migration/` | WASM checkpoint/restore | Injected WASM compute module | `cold` | `WASM-migration/README.md` |

Shared nodes:
- Source: `edge-node-1`
- Destination: `edge-node-2`
- Relay / host-hop: `edge-host-1`

Transfer modes:
- `host`: source -> relay -> destination when `--relay-node edge-host-1` is passed. Without relay, your host machine is used.
- `direct`: source -> destination using VM-to-VM SSH/SCP. Orchestrators set SSH trust automatically.

Archive transfer mechanics:
- `--transfer-mode direct`: the source VM sends the archive straight to the destination VM with `scp`.
- `--transfer-mode host` without `--relay-node`: the orchestrator uses `multipass transfer` from source VM to a temporary file on the host machine, then `multipass transfer` from the host machine to the destination VM.
- `--transfer-mode host --relay-node edge-host-1`: the orchestrator does not use `multipass transfer` for the archive path. It sets SSH trust and uses `scp` twice: source VM -> relay VM, then relay VM -> destination VM.
- The relay only stages checkpoint archives. It does not participate in CRIU post-copy page faults.

Post-copy lazy-pages traffic:
- Source starts `criu dump --lazy-pages --address 0.0.0.0 --port <port>` and keeps that CRIU process alive as the source-side page server.
- Destination unpacks the archive, starts `criu lazy-pages -D <dir> --page-server --address <source-ip> --port <port>`, then restores with `criu restore --lazy-pages`.
- Despite the `--page-server` flag name, the `criu lazy-pages` process runs on the destination in this repo. It connects back to the source page server and services local page faults for the restored process.
- Lazy memory pages flow source -> destination over that TCP connection. They do not flow source -> relay -> destination.
- A relay/mainframe can cache full cold/pre-copy archives and resend them to another compatible node. For post-copy, the initial archive alone is not a durable complete image while pages are still lazy; restoring to a later or different node still requires the original source page server to be alive and reachable, or a later full checkpoint containing all pages.

Metrics:
- CRIU modules use same CSV schema and support cross-experiment plots.
- WASM writes compatible timing rows for comparison, but only `cold` strategy exists.
- Podman baseline appends to `Container/metrics/migration_metrics.csv`.

## 1) Host Setup

Host machine needs:
- `multipass` with KVM/QEMU virtualization
- `terraform`
- `python3`
- `ssh` / `scp`
- `curl`
- `git`

Quick checks:

```bash
multipass version
terraform version
python3 --version
ssh -V
curl --version
```

Provision three VMs:

```bash
cd tools/terraform
terraform init
terraform apply -auto-approve
cd ../..
```

Verify nodes:

```bash
multipass list
```

Readiness source of truth:

```bash
bash tools/terraform/check_bootstrap.sh
```

Run this before any benchmark. It waits until all three nodes are reachable, `node-bootstrap` logged `Node fully provisioned.`, and CRIU/Podman commands are available.

Terraform/bootstrap details:
- `tools/terraform/README.md`

## 2) Optional Metrics Setup

Install and check node_exporter on all nodes:

```bash
bash Container/scripts/setup/install_node_exporter.sh edge-node-1 edge-node-2 edge-host-1
bash Container/scripts/setup/check_node_exporter_metrics.sh edge-node-1 edge-node-2 edge-host-1
bash Container/scripts/setup/check_time_sync.sh edge-node-1 edge-node-2 edge-host-1
```

Use `--snapshot-node-metrics` in suite runners to capture before/after node metrics.

## 3) One-Run Smoke Commands

Use these to verify each benchmark path after provisioning.

### Counter CRIU

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_counter_c.sh edge-node-1

python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct
```

Switch strategy:

```bash
python3 Container/scripts/orchestrators/criu_benchmark.py precopy \
  --source edge-node-1 --dest edge-node-2 \
  --transfer-mode direct --iterations 2

python3 Container/scripts/orchestrators/criu_benchmark.py postcopy \
  --source edge-node-1 --dest edge-node-2 \
  --transfer-mode direct --page-server-port 9999
```

Deep docs:
- `Container/CRIU-COLD-MIGRATION.md`
- `Container/CRIU-PRE-COPY.md`
- `Container/CRIU-POST-COPY.md`

### Podman + CRIU Container

Podman path is baseline container migration, not same native-process orchestrator.

Manual demo:
- `Container/PODMAN-MIGRATION.md`

CSV collection:

```bash
bash Container/scripts/orchestrators/collect_podman_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter
```

### Game of Life CRIU

```bash
python3 Game-of-life-migration/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1

python3 Game-of-life-migration/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct
```

Switch strategy:

```bash
python3 Game-of-life-migration/scripts/orchestrators/criu_benchmark.py precopy \
  --source edge-node-1 --dest edge-node-2 \
  --transfer-mode direct --iterations 2

python3 Game-of-life-migration/scripts/orchestrators/criu_benchmark.py postcopy \
  --source edge-node-1 --dest edge-node-2 \
  --transfer-mode direct --page-server-port 9999
```

Deep doc:
- `Game-of-life-migration/README.md`

### TCP Client CRIU

TCP migration needs server plus VIP handoff so restored client keeps same local IP.

```bash
export TCP_PORT=5000
export TCP_VIP=10.22.132.250

python3 Network-live-migration/scripts/setup/reset_nodes.py edge-node-1 edge-node-2 edge-host-1 "$TCP_VIP"
bash Network-live-migration/scripts/workloads/start_tcp_server.sh edge-host-1 "$TCP_PORT"
TCP_VIP="$TCP_VIP" bash Network-live-migration/scripts/workloads/start_tcp_client.sh edge-node-1 edge-host-1 "$TCP_PORT"

python3 Network-live-migration/scripts/orchestrators/tcp_client_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --server edge-host-1 \
  --transfer-mode host \
  --relay-node edge-host-1
```

Switch strategy:

```bash
python3 Network-live-migration/scripts/orchestrators/tcp_client_benchmark.py precopy \
  --source edge-node-1 --dest edge-node-2 --server edge-host-1 \
  --transfer-mode host --relay-node edge-host-1 --iterations 2

python3 Network-live-migration/scripts/orchestrators/tcp_client_benchmark.py postcopy \
  --source edge-node-1 --dest edge-node-2 --server edge-host-1 \
  --transfer-mode host --relay-node edge-host-1 --page-server-port 9999
```

Deep docs:
- `Network-live-migration/TCP-live-migration.md`
- `Network-live-migration/CRIU-limitations.md`

### WASM Checkpoint/Restore

Install WASM runtime deps on nodes:

```bash
bash WASM-migration/scripts/setup/install_wasm_node_deps.sh edge-node-1 edge-node-2 edge-host-1
```

Run one migration:

```bash
python3 WASM-migration/scripts/orchestrators/wasm_benchmark.py \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode host \
  --relay-node edge-host-1 \
  --profile-name smoke
```

Direct transfer:

```bash
python3 WASM-migration/scripts/orchestrators/wasm_benchmark.py \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --profile-name smoke
```

Deep docs:
- `WASM-migration/README.md`
- `WASM-migration/simplest-example/README.md`

## 4) Repeatable Benchmark Suites

Use suites for real measurements. They reset nodes, restart workload, append CSV rows, write logs, and optionally generate plots.

### Counter

```bash
python3 Container/scripts/orchestrators/repeat_benchmarks.py suite \
  --strategies cold,precopy,postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --relay-node edge-host-1 \
  --host-runs 10 \
  --direct-runs 10 \
  --iterations 2 \
  --snapshot-node-metrics
```

### Game of Life

```bash
python3 Game-of-life-migration/scripts/orchestrators/repeat_benchmarks.py suite \
  --strategies cold,precopy,postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --relay-node edge-host-1 \
  --host-runs 10 \
  --direct-runs 10 \
  --iterations 2 \
  --snapshot-node-metrics
```

### TCP Client

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

### WASM

```bash
python3 WASM-migration/scripts/orchestrators/repeat_wasm_benchmarks.py suite \
  --strategies cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --relay-node edge-host-1 \
  --host-runs 10 \
  --direct-runs 10 \
  --snapshot-node-metrics \
  --profile-name smoke
```

## 5) Artifacts

| Benchmark | Timing CSV | Run logs | Plots | Node snapshots |
|-----------|------------|----------|-------|----------------|
| Counter / Podman | `Container/metrics/migration_metrics.csv` | `Container/metrics/run_logs/` | `Container/metrics/plots/` | `Container/metrics/node_exporter/` |
| Game of Life | `Game-of-life-migration/metrics/migration_metrics.csv` | `Game-of-life-migration/metrics/run_logs/` | `Game-of-life-migration/metrics/plots/` | `Game-of-life-migration/metrics/node_exporter/` |
| TCP Client | `Network-live-migration/metrics/migration_metrics.csv` | `Network-live-migration/metrics/run_logs/` | `Network-live-migration/metrics/plots/` | `Network-live-migration/metrics/node_exporter/` |
| WASM | `WASM-migration/metrics/migration_metrics.csv` | `WASM-migration/metrics/run_logs/` | `WASM-migration/metrics/plots/` | `WASM-migration/metrics/node_exporter/` |

Core CSV fields:
- `checkpoint_ms`
- `archive_bytes`
- `transfer_ms`
- `restore_ms`
- `downtime_ms`
- `bandwidth_mbps`
- `success`
- `notes`
- `profile_name`

Metrics docs:
- `Container/metrics/README.md`
- `Game-of-life-migration/metrics/README.md`
- `Network-live-migration/metrics/README.md`
- `WASM-migration/metrics/README.md`

## 6) Plot Generation

Suite runners generate plots unless `--no-plots` is used.

Manual plot commands:

```bash
python3 Container/scripts/visualization/generate_all_plots.py \
  --csv Container/metrics/migration_metrics.csv \
  --run-ids-file Container/metrics/run_logs/<batch>.run_ids.txt \
  --out-dir Container/metrics/plots/<batch>

python3 Game-of-life-migration/scripts/visualization/generate_all_plots.py \
  --csv Game-of-life-migration/metrics/migration_metrics.csv \
  --run-ids-file Game-of-life-migration/metrics/run_logs/<batch>.run_ids.txt \
  --out-dir Game-of-life-migration/metrics/plots/<batch>

python3 Network-live-migration/scripts/visualization/generate_all_plots.py \
  --csv Network-live-migration/metrics/migration_metrics.csv \
  --run-ids-file Network-live-migration/metrics/run_logs/<batch>.run_ids.txt \
  --out-dir Network-live-migration/metrics/plots/<batch>

python3 WASM-migration/scripts/visualization/generate_all_plots.py \
  --csv WASM-migration/metrics/migration_metrics.csv \
  --run-ids-file WASM-migration/metrics/run_logs/<batch>.run_ids.txt \
  --out-dir WASM-migration/metrics/plots/<batch>
```

## 7) Network Profile Sweeps

`run_all.py` runs suites from `benchmarks.json` across `network_profiles.json`.

```bash
python3 run_all.py
```

Useful options:

```bash
python3 run_all.py --profiles 1_TEE_Best,2_TEE_Avg
python3 run_all.py --continue-on-failure
python3 run_all.py --cooldown-seconds 20
```

Notes:
- `run_all.py` applies `tc` rules on default VM interfaces.
- Rules target VM-to-VM traffic, so host-to-VM control traffic stays responsive.
- Suite commands receive `--profile-name`, saved in CSV.

## 8) Troubleshooting Map

General node cleanup:

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
python3 Network-live-migration/scripts/setup/reset_nodes.py edge-node-1 edge-node-2 edge-host-1 10.22.132.250
```

Common CRIU logs:

```bash
multipass exec edge-node-1 -- sudo tail -n 120 /tmp/CRIU-counter/dump.log
multipass exec edge-node-2 -- sudo tail -n 120 /tmp/CRIU-counter/restore.log
multipass exec edge-node-2 -- sudo tail -n 120 /tmp/CRIU-counter/lazy-pages.log
```

Diagnose helper:

```bash
bash Container/scripts/helpers/diagnose_migration.sh --source edge-node-1 --dest edge-node-2
```

Post-copy page-server conflict:
- Re-run reset script.
- Or choose different `--page-server-port`.

TCP restore/socket issues:
- Read `Network-live-migration/CRIU-limitations.md`.
- Verify VIP: `10.22.132.250`.

WASM build/runtime issues:
- Read `WASM-migration/README.md`.
- Check `WASM-migration/wasm-migrate-commands/LOCAL_CHANGES.md`.

## 9) Where To Go Next

Use this guide for orchestration overview. Use specific docs for exact details:

- Counter CRIU: `Container/README.md`
- Cold CRIU: `Container/CRIU-COLD-MIGRATION.md`
- Pre-copy CRIU: `Container/CRIU-PRE-COPY.md`
- Post-copy CRIU: `Container/CRIU-POST-COPY.md`
- Podman container: `Container/PODMAN-MIGRATION.md`
- Game of Life: `Game-of-life-migration/README.md`
- TCP migration: `Network-live-migration/TCP-live-migration.md`
- WASM migration: `WASM-migration/README.md`
