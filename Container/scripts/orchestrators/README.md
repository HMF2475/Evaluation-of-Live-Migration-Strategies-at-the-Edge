# Orchestrators

## Overview

Main test runners that automate complete migration experiments (CRIU native process migration and Podman+CRIU container migration).

For end-to-end benchmark instructions, see `GUIDE.md`.

## Layout

The CRIU framework uses a strategy pattern to implement different migration methods:

```
orchestrators/
├── criu_benchmark.py          - Main entry point (CLI + orchestration)
├── repeat_benchmarks.py       - Repeat runs (host/direct) + log capture
├── migration_strategy.py       - Abstract base class
├── cold_migration.py           - Cold migration strategy
├── precopy_migration.py        - Precopy migration strategy 
├── postcopy_migration.py       - Postcopy migration strategy (lazy-pages, experimental)
├── multipass_command.py        - VM command execution wrapper
├── ssh_utils.py                - SSH/SCP utilities
└── metrics.py                  - Unified metrics dataclass
```

## Key Scripts

- `criu_benchmark.py` — runs one CRIU migration (`cold`, `precopy`, `postcopy`).
- `repeat_benchmarks.py` — repeats runs in `host` and/or `direct` modes, resets nodes between runs, and optionally generates plots.
- `run_memory_only_suite_30.sh` — convenience runner (memory-only counter workload).
- `collect_podman_metrics.sh` — Podman+CRIU container migration benchmark.

## Common Usage

```bash
# One run (cold)
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode host \
  --run-id experimental-cold-0001

# One run (pre-copy)
python3 Container/scripts/orchestrators/criu_benchmark.py precopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode host \
  --iterations 2 \
  --run-id experimental-precopy-0001

# Repeat runs and generate plots (default); disable plots with --no-plots
python3 Container/scripts/orchestrators/repeat_benchmarks.py suite \
  --source edge-node-1 \
  --dest edge-node-2 \
  --host-runs 10 \
  --direct-runs 10 \
  --iterations 2

# Podman container migration benchmark
bash Container/scripts/orchestrators/collect_podman_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter
```

## Outputs

- All orchestrators append one row per run to `Container/metrics/migration_metrics.csv`.
- `repeat_benchmarks.py` writes per-suite logs to `Container/metrics/run_logs/` and plots to `Container/metrics/plots/`.
- If `repeat_benchmarks.py --snapshot-node-metrics` is used, raw snapshots are stored under `Container/metrics/node_exporter/` and a per-run CSV is appended to `Container/metrics/node_exporter_metrics.csv`.

## Notes

- `--transfer-mode host|direct` changes only how the CRIU image archive is transferred; post-copy still requires VM→VM connectivity for the page-server.
- `--relay-node edge-host-1` makes `host` mode use the third VM as the intermediate hop instead of the laptop.
- `direct` uses VM→VM `scp` and requires SSH trust; the orchestrator sets this up automatically (see `Container/scripts/orchestrators/ssh_utils.py`).
- After a successful restore, the orchestrators write `/home/ubuntu/counter.pid` and `/home/ubuntu/app.pid` on the destination, so you can “bounce” the workload back and forth without re-running the workload launcher.

For the full CSV schema and plot outputs, see `Container/metrics/README.md`.
