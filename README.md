# Evaluation of Live Migration Strategies at the Edge 

This repository contains the implementation and experiments for evaluating **service migration strategies in tactical/edge environments**, with a focus on comparing:
- **CRIU-based migration** (native process + containers via Podman/CRIU), and
- **WebAssembly (WASM) migration**.

## Prerequisites

### Host System Requirements

To run this project, your host system must have:

- **Terraform** (v1.0 or newer) – Infrastructure provisioning
- **Multipass** (v1.13 or newer) – VM provisioning and management
- **Python 3.8+** – Required for benchmark orchestration scripts
- **SSH/SCP** – For inter-VM file transfers and remote command execution
- **Git** – For cloning the repository
- **curl** – For node_exporter health checks
- **KVM/QEMU** – Hypervisor support (required by Multipass)

### Installation (Ubuntu/Debian example)

```bash
# Install Terraform
wget -O terraform.zip https://releases.hashicorp.com/terraform/1.x.x/terraform_1.x.x_linux_amd64.zip
unzip terraform.zip && sudo mv terraform /usr/local/bin/

# Install Multipass
snap install multipass

# Install Python + dependencies
sudo apt-get install python3 python3-pip curl git
pip3 install paramiko  # required by benchmark scripts
```

For installation on other operating systems, see:
- [Terraform Install Guide](https://www.terraform.io/downloads)
- [Multipass Install Guide](https://multipass.run/install)

## Start Here

- **Exhaustive end-to-end manual** (setup → run → metrics → plots): `GUIDE.md`
- **Container/CRIU tooling entrypoint** (folder index + key scripts): `Container/README.md`

## Repository Structure (high level)

- `Container/` — CRIU + Podman migration experiments, scripts, metrics, and plots
- `Game-of-life-migration/` — CRIU migration experiments for the Game of Life workload (C implementation)
- `Network-live-migration/` — CRIU TCP client migration (established socket + VIP handoff), metrics, and plots
- `tools/terraform/` — Multipass VM provisioning (`edge-node-1`, `edge-node-2`, `edge-host-1`)
- `Papers/` — research context / related work


## Quick Sanity Run (All Workloads)

After provisioning the VMs (see `GUIDE.md`), you can run a small batch for any of the three main workloads:

- **Counter** (memory-only baseline):
  ```bash
  python3 Container/scripts/orchestrators/repeat_benchmarks.py suite \
    --strategies cold,precopy,postcopy \
    --source edge-node-1 \
    --dest edge-node-2 \
    --relay-node edge-host-1 \
    --host-runs 1 \
    --direct-runs 1 \
    --iterations 2 \
    --snapshot-node-metrics
  ```
- **Game of Life** (C application):
  ```bash
  python3 Game-of-life-migration/scripts/orchestrators/repeat_benchmarks.py suite \
    --strategies cold,precopy,postcopy \
    --source edge-node-1 \
    --dest edge-node-2 \
    --relay-node edge-host-1 \
    --host-runs 1 \
    --direct-runs 1 \
    --iterations 2 \
    --snapshot-node-metrics
  ```
- **TCP client/server** (socket migration):
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
    --host-runs 1 \
    --direct-runs 1 \
    --snapshot-node-metrics
  ```

Results are appended to the corresponding `metrics/migration_metrics.csv` in each module.
Plots and batch logs are written under each module's `metrics/plots/` and `metrics/run_logs/`.
# (add section at end)

## Supported Workloads

This repository supports three main migration workloads:

- **Counter**: A minimal C program that prints incrementing numbers to stdout. Used as a memory-only baseline for CRIU migration.
- **Game of Life**: A C implementation of Conway's Game of Life, printing an evolving grid to stdout. Used to test migration of a non-trivial, stateful application. See `Game-of-life-migration/`.
- **TCP client/server**: An echo server/client pair for testing migration of established TCP connections (socket state preservation). See `Network-live-migration/`.

## Run Everything Across Network Profiles (Optional)

To sweep multiple network conditions (bandwidth/latency/loss) and run the main suites automatically:

```bash
python3 run_all.py
```

This uses:
- `network_profiles.json` (profiles)
- `benchmarks.json` (suite registry)

See `GUIDE.md` for details and options.

## Development & Linting

This project uses **Ruff** for Python to maintain code quality.

### Installing

You can install Ruff using pip:
```bash
pip install ruff
```

### Running Linters & Fixers

You can run Ruff using the standard commands:

- `ruff check .` - Check for issues.
- `ruff check --fix .` - Automatically fix safe issues (unused imports, whitespace, etc).
- `ruff format .` - Format the code.

### Pre-commit Hooks (Optional)

The repository includes a `.pre-commit-config.yaml`. You can ensure files are automatically linted before committing by setting up pre-commit:

```bash
pip install pre-commit
pre-commit install
```
Once installed, every `git commit` will automatically run Ruff on your staged changes.
