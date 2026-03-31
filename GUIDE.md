# Exhaustive Benchmark Guide (CRIU / Podman / Metrics / Plots)

This guide is the **main end-to-end manual** for running migration benchmarks in this repository:
- Provision the 2 Multipass nodes (`edge-node-1` → `edge-node-2`)
- Run single migrations (cold / pre-copy / post-copy)
- Run repeated benchmark batches (host vs direct transfer)
- Collect node_exporter metrics (CPU / memory / disk IO)
- Generate plots and locate all artifacts

If you only want folder-specific details, each subdirectory has a scoped README:
- `Container/README.md`
- `Container/scripts/orchestrators/README.md`
- `Container/scripts/workloads/README.md`
- `Container/scripts/setup/README.md`
- `Container/scripts/visualization/README.md`
- `Container/metrics/README.md`

---

## 0) Concepts and Terminology

### Nodes
- **Source** node: `edge-node-1`
- **Destination** node: `edge-node-2`

### Strategies (optimization)
- `cold`: freeze → dump → transfer → restore (baseline, highest downtime)
- `precopy`: pre-dumps while running + final dump/restore (reduced downtime)
- `postcopy`: `lazy-pages` (restore quickly, fetch pages on-demand; **experimental**)

### Transfer modes (channel)
- `host`: source → host → destination (via `multipass transfer`)
- `direct`: source → destination (via SSH/SCP between the VMs)

### Workloads
- `counter` (recommended baseline): a tiny C program that prints `0,1,2...` to stdout
  - Captured on the VM at `/home/ubuntu/counter.out`
- `tcp` / `udp` (experimental): echo servers intended for socket migration experiments

### Run IDs
The default scheme used by the repeat runner (and by `criu_benchmark.py` when you omit `--run-id`) is:

`DD-MM-YYYY-(host|direct)-(cold|precopy|postcopy)-NNNN`

Example: `30-03-2026-direct-precopy-0007`

### What is measured
Every run appends a row to `Container/metrics/migration_metrics.csv` with:
- `checkpoint_ms`: freeze/dump time (for `precopy`: **final dump only**)
- `transfer_ms`: archive transfer time
- `restore_ms`: restore time
- `downtime_ms`: `checkpoint_ms + transfer_ms + restore_ms`

**Verification note**: logs may show `Expected min` (frozen_last + 1) and `Observed` after a short wait, so `Observed` is usually higher.

---

## 1) One-time Infrastructure Setup (Multipass + Terraform)

Provision the two VMs:

```bash
cd tools/terraform
terraform init
terraform apply -auto-approve
cd ../..
```

Verify both VMs exist and are reachable:

```bash
multipass list
multipass exec edge-node-1 -- uname -a
multipass exec edge-node-2 -- uname -a
multipass exec edge-node-1 -- criu --version
multipass exec edge-node-2 -- criu --version
```

If your Terraform/Multipass bootstrap installs run in the background, follow:
- `tools/terraform/README.md`

---

## 2) Optional (Recommended): node_exporter + Time Sync

Install node_exporter on both nodes (CPU/mem/disk IO metrics):

```bash
bash Container/scripts/setup/install_node_exporter.sh edge-node-1 edge-node-2
```

Sanity-check node_exporter output:

```bash
bash Container/scripts/setup/check_node_exporter_metrics.sh edge-node-1 edge-node-2
```

Check host vs node clocks + NTP status:

```bash
bash Container/scripts/setup/check_time_sync.sh edge-node-1 edge-node-2
```

---

## 3) Single-Run Benchmarks (Manual Flow)

All strategies follow the same high-level pattern:
1) Reset nodes (cleanup)
2) Start workload on the source node
3) Run migration strategy
4) Inspect outputs

### 3.1 Reset nodes

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
```

### 3.2 Start the baseline workload (counter)

```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1
```

Watch it live:

```bash
multipass exec edge-node-1 -- tail -f /home/ubuntu/counter.out
```

### 3.3 Cold migration

```bash
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct
```

CLI reference:
```bash
python3 Container/scripts/orchestrators/criu_benchmark.py --help
```

### 3.4 Pre-copy migration

```bash
python3 Container/scripts/orchestrators/criu_benchmark.py precopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --iterations 2
```

### 3.5 Post-copy migration (lazy-pages, experimental)

Requirements:
- Destination must be able to reach the source VM by IP/port (page-server).
- Kernel/CRIU must support `lazy-pages` (uses `userfaultfd` internally).

Run:

```bash
python3 Container/scripts/orchestrators/criu_benchmark.py postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --page-server-port 9999
```

Troubleshooting for post-copy is in:
- `Container/CRIU-POST-COPY.md`

---

## 4) Repeatable Benchmark Batches (Recommended)

Use `repeat_benchmarks.py` to run N migrations in `host` mode and N in `direct` mode.
It automatically:
- resets nodes before each run
- restarts the workload before each run
- optionally snapshots node_exporter metrics before/after each run
- optionally generates plots at the end

CLI reference:
```bash
python3 Container/scripts/orchestrators/repeat_benchmarks.py --help
```

### 4.1 Run a suite (cold + precopy + postcopy)

```bash
python3 Container/scripts/orchestrators/repeat_benchmarks.py suite \
  --strategies cold,precopy,postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --workload counter \
  --network-migration no \
  --host-runs 10 \
  --direct-runs 10 \
  --iterations 2 \
  --snapshot-node-metrics
```

### 4.2 Memory-only “30 tests” batch (copy/paste)

```bash
python3 Container/scripts/orchestrators/repeat_benchmarks.py suite \
  --strategies cold,precopy,postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --workload counter \
  --network-migration no \
  --host-runs 30 \
  --direct-runs 30 \
  --iterations 5 \
  --snapshot-node-metrics
```

### 4.3 Key batch artifacts

For each batch, `repeat_benchmarks.py` writes:
- Raw log: `Container/metrics/run_logs/<batch>.log`
- Run list: `Container/metrics/run_logs/<batch>.run_ids.txt` (used for plot filtering)
- Plots: `Container/metrics/plots/<batch>/`

The `<batch>` name is auto-generated to be meaningful (date + workload + strategies + counts, etc.). Override it with `--base-run-id` if you want a custom name.

Disable plots:
```bash
python3 Container/scripts/orchestrators/repeat_benchmarks.py suite ... --no-plots
```

---

## 5) Metrics and Where Things Are Saved

### 5.1 Main CSV (migration timing)
- `Container/metrics/migration_metrics.csv`

### 5.2 node_exporter snapshots (raw)
When you use `--snapshot-node-metrics`, snapshots are written under:
- `Container/metrics/node_exporter/<run_id>/`

### 5.3 node_exporter summary CSV (derived)
The repeat runner also appends a per-run summary row to:
- `Container/metrics/node_exporter_metrics.csv`

See schema + interpretation:
- `Container/metrics/README.md`

---

## 6) Plot Generation

Batch plotting (recommended):

```bash
python3 Container/scripts/visualization/generate_all_plots.py \
  --csv Container/metrics/migration_metrics.csv \
  --run-ids-file Container/metrics/run_logs/<batch>.run_ids.txt \
  --out-dir Container/metrics/plots/<batch>
```

The repeat runner runs this automatically unless you pass `--no-plots`.

---

## 7) Network-aware Experiments (Experimental)

Workloads:
- TCP echo: `bash Container/scripts/workloads/start_tcp_echo.sh edge-node-1 5000`
- UDP echo: `bash Container/scripts/workloads/start_udp_echo.sh edge-node-1 5001`

Enable CRIU socket options:
- `--network-migration yes`
- (often) `--ext-net-map SRC_IP:DST_IP`

Example (TCP + cold):
```bash
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --network-migration yes \
  --ext-net-map 10.0.0.1:10.0.0.2
```

Note: exact requirements depend on the workload’s network namespace and addresses.

---

## 8) Podman+CRIU Container Migration Baseline

Canonical, copy/paste demo:
- `Container/PODMAN-MIGRATION.md`

Scripted collection into the same CSV schema:
```bash
bash Container/scripts/orchestrators/collect_podman_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter
```

---

## 9) Troubleshooting and Debugging

### Common logs
- Source dump logs: `multipass exec edge-node-1 -- sudo tail -n 120 /tmp/CRIU-counter/dump.log`
- Destination restore logs: `multipass exec edge-node-2 -- sudo tail -n 120 /tmp/CRIU-counter/restore.log`
- Post-copy lazy-pages logs (dest): `multipass exec edge-node-2 -- sudo tail -n 120 /tmp/CRIU-counter/lazy-pages.log`

### Diagnose script (after failures)
```bash
bash Container/scripts/helpers/diagnose_migration.sh --source edge-node-1 --dest edge-node-2
```

### Post-copy: “Address already in use”
The page-server port may be held by a previous failed run. Fix:
- re-run `reset_nodes.py`, or
- choose a different port with `--page-server-port`.

---

## 10) Strategy Guides (Repo-specific)

Use these when you want the exact CRIU flags and the detailed “manual” flow:
- Cold: `Container/CRIU-COLD-MIGRATION.md`
- Pre-copy: `Container/CRIU-PRE-COPY.md`
- Post-copy: `Container/CRIU-POST-COPY.md`
