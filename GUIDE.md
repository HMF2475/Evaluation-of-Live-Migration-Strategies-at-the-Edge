# Experiment Framework and Scripts Reference

A comprehensive reference for running service migration benchmarks in this project.

## 1. Benchmarking Scope

The framework evaluates service migration approaches for edge computing environments, with current focus on checkpoint/restore-based container migration using CRIU across two Ubuntu VMs (`edge-node-1` → `edge-node-2`).

### Supported Migration Methods

- **Native CRIU**: Cold migration, pre-copy live migration
- **Post-Copy**: Experimental (TODO: lazy-pages daemon implementation)
- **P.Haul**: Not yet integrated (TODO: requires Go library bindings)
- **Podman+CRIU**: Container-based migration baseline

## 2. Repository Organization

| Directory | Purpose |
|-----------|---------|
| `tools/terraform/` | Infrastructure provisioning (Multipass VMs) |
| `Container/scripts/` | Experiment automation scripts |
| `Container/metrics/` | Benchmark results (CSV format) |
| `Container/*.md` | Strategy-specific documentation |

## 3. System Components

**Multipass**
Creates and manages Ubuntu VM instances for benchmark experiments.

**Terraform**
Automates provisioning of Multipass VMs and initial configuration.

**CRIU (Checkpoint/Restore In Userspace)**
Core checkpoint/restore engine. Performs process state capture and restoration.

**Podman**
Container runtime providing checkpoint/restore interface via CRIU.

**checkpointctl**
Analysis tool for CRIU checkpoint archives and metadata inspection.

**P.Haul (Not Yet Implemented)**
CRIU's official live migration library. Requires Go interface implementation (PhaulLocal, PhaulRemote) with RPC communication. See reference implementation in https://criu.org/P.Haul.

**Scripts**
Python and Bash utilities for experiment orchestration, metrics collection, and diagnostics.

## 4. Active Scripts

### Setup

**`Container/scripts/setup/reset_nodes.py`**
- Cleans both nodes before experiments
- Removes stale PID files, logs, and checkpoint directories
- Idempotent operation

### Orchestrators

**`Container/scripts/orchestrators/criu_benchmark.py`**
- Automated CRIU migration benchmark runner
- Strategies: `cold`, `precopy`, `postcopy`
- Measurements: checkpoint/transfer/restore times, downtime, bandwidth
- Output: CSV metrics to `Container/metrics/migration_metrics.csv`

**`Container/scripts/orchestrators/collect_podman_metrics.sh`**
- Podman container checkpoint/restore automation
- Unified CSV schema with native CRIU benchmarks

### Workloads

**`Container/scripts/workloads/start_counter_c.sh`**
- Deploys and runs simple counter workload (baseline)
- Generates `/home/ubuntu/counter.pid` and `/home/ubuntu/counter.log`
- Recommended for standard benchmarks

**`Container/scripts/workloads/start_tcp_echo.sh`** **_EXPERIMENTAL, NOT YET TESTED IN MIGRATION_**
- TCP echo server (network-aware migration testing)
- Creates `/home/ubuntu/app.pid`

**`Container/scripts/workloads/start_udp_echo.sh`** **_EXPERIMENTAL, NOT YET TESTED IN MIGRATION_**
- UDP echo server (network-aware migration testing)
- Creates `/home/ubuntu/app.pid`

### Helpers

**`Container/scripts/helpers/diagnose_migration.sh`**
- Post-migration diagnostics
- Verifies connectivity, CRIU availability, dump integrity, restore success

**`Container/scripts/helpers/validate_migration.py`**
- Checkpoint validation using `checkpointctl`
- Verifies checkpoint archive structure and completeness

### CSV Schema

All benchmarks write to a unified 16-column CSV:

```
run_id, technology, migration_method, network_migration, checkpoint_ms,
archive_bytes, transfer_ms, restore_ms, downtime_ms, bandwidth_mbps,
src_arch, dst_arch, same_arch, success, notes, timestamp
```

## 5. Prerequisites

### Infrastructure Setup

Initialize Multipass VMs with Terraform:

```bash
cd tools/terraform
terraform init
terraform apply -auto-approve
cd ../..
```

### Verification

Verify both nodes are ready:

```bash
multipass list
multipass exec edge-node-1 -- criu --version
multipass exec edge-node-2 -- criu --version
```

### Optional Python Dependencies

For metrics analysis and visualization:

```bash
python3 -m pip install pandas seaborn matplotlib
```

## 6. Benchmark Procedures

### A. Native CRIU Cold Migration (Baseline)

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_counter_c.sh edge-node-1

python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --run-id baseline-cold-001
```

### B. Native CRIU Pre-Copy Live Migration

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_counter_c.sh edge-node-1

python3 Container/scripts/orchestrators/criu_benchmark.py precopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --iterations 2 \
  --run-id baseline-precopy-001
```

### C. Native CRIU Post-Copy Live Migration (Experimental)

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_counter_c.sh edge-node-1

python3 Container/scripts/orchestrators/criu_benchmark.py postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --run-id experimental-postcopy-001
```

Note: Post-copy is experimental and currently non-functional. See `Container/CRIU-POST-COPY.md` for implementation notes.

### D. Podman+CRIU Container Migration

Start container on source:

```bash
multipass exec edge-node-1 -- sudo podman run -d \
  --name counter \
  --network=none \
  --security-opt apparmor=unconfined \
  busybox:latest \
  sh -c 'i=0; while true; do echo $i; i=$((i+1)); sleep 1; done'
```

Run automation:

```bash
bash Container/scripts/orchestrators/collect_podman_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter \
  --network-migration no \
  --transfer-mode direct \
  --run-id podman-baseline-001 \
  --csv Container/metrics/migration_metrics.csv
```

### E. Network-Aware Benchmarks (TO BE TESTED)

TCP echo server migration:

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_tcp_echo.sh edge-node-1 5000
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 --dest edge-node-2 --run-id tcp-cold-001
```

UDP echo server migration:

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_udp_echo.sh edge-node-1 5001
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 --dest edge-node-2 --run-id udp-cold-001
```

## 7. Diagnostics and Validation

When migration fails, run diagnostics:

```bash
bash Container/scripts/helpers/diagnose_migration.sh \
  --source edge-node-1 \
  --dest edge-node-2
```

To validate checkpoint archives:

```bash
python3 Container/scripts/helpers/validate_migration.py /tmp/CRIU-counter
```

Direct `checkpointctl` inspection:

```bash
checkpointctl show <checkpoint-archive>
checkpointctl inspect <checkpoint-archive>
```

## 8. Metrics and Results

### CSV Output Format

Results are appended to `Container/metrics/migration_metrics.csv` with the unified schema:

| Column | Description |
|--------|-------------|
| `migration_method` | `cold`, `precopy`, or `postcopy` |
| `network_migration` | `yes` (sockets) or `no` (memory-only) |
| `checkpoint_ms` | Dump phase duration |
| `transfer_ms` | Archive transfer duration |
| `restore_ms` | Restore phase duration |
| `downtime_ms` | Total service unavailability (checkpoint + transfer + restore) |
| `archive_bytes` | Checkpoint image size |
| `bandwidth_mbps` | Effective transfer bandwidth |
| `success` | Migration result (true/false) |
| `notes` | Error description or special conditions |
| `timestamp` | Benchmark execution time |

### Interpretation

- **Downtime**: Primary metric for service continuity assessment
- **Archive Size**: Indicator of workload memory footprint and storage requirements
- **Bandwidth**: Network efficiency; typically limited by CPU (dump/restore) rather than network I/O at small scales
- **Method Comparison**: Cold vs precopy downtime reduction indicates live migration effectiveness

--- 

## Additional Resources

- **Strategy-specific guides**: See `Container/CRIU-COLD-MIGRATION.md`, `Container/CRIU-PRE-COPY.md`, and `Container/CRIU-POST-COPY.md`
- **Script documentation**: Each script folder (`setup/`, `orchestrators/`, `helpers/`, `workloads/`) contains a README with detailed usage information
