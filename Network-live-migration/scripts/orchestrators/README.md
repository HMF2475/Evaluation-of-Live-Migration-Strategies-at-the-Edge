# Orchestrators

## Overview

Main test runners that automate CRIU migrations under `Network-live-migration/`.

For the TCP experiment walkthrough, see `Network-live-migration/TCP-live-migration.md`.

## Layout

The CRIU framework uses a strategy pattern to implement different migration methods.

```
orchestrators/
├── migration_strategy.py        - Base strategy helpers (transfer, arch checks, etc.)
├── tcp_client_migration.py      - TCP client cold/precopy/postcopy implementations
├── tcp_client_benchmark.py      - Single-run CLI entrypoint
├── repeat_tcp_client_benchmarks.py - Suite runner (host+direct), optional snapshots/plots
├── multipass_command.py         - VM command execution wrapper
├── ssh_utils.py                 - SSH/SCP utilities (direct and relay transfers)
├── node_exporter_metrics.py     - node_exporter snapshot integration
└── metrics.py                   - Unified metrics dataclass (CSV-compatible)
```

## Key Scripts

- `tcp_client_benchmark.py` — migrate a running TCP client (`cold|precopy|postcopy`).
- `repeat_tcp_client_benchmarks.py` — run suites (host+direct), optional node_exporter snapshots, optional plots.

## Common Usage

```bash
# One run (cold TCP client migration)
python3 Network-live-migration/scripts/orchestrators/tcp_client_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --server edge-host-1 \
  --transfer-mode host \
  --relay-node edge-host-1

# Repeat a suite (cold + precopy + postcopy, host+direct)
python3 Network-live-migration/scripts/orchestrators/repeat_tcp_client_benchmarks.py \
  --source edge-node-1 \
  --dest edge-node-2 \
  --server edge-host-1 \
  --relay-node edge-host-1 \
  --host-runs 10 \
  --direct-runs 10 \
  --iterations 2 \
  --snapshot-node-metrics
```

## Outputs

- One row per run is appended to `Network-live-migration/metrics/migration_metrics.csv`.
- Suite logs are written under `Network-live-migration/metrics/run_logs/`.
- Plot output is written under `Network-live-migration/metrics/plots/`.
- If snapshots are enabled, raw node_exporter snapshots are stored under `Network-live-migration/metrics/node_exporter/` and summary rows are appended to `Network-live-migration/metrics/node_exporter_metrics.csv`.

## Notes

- `--transfer-mode host|direct` changes only how the CRIU image archive is transferred; post-copy still requires VM→VM connectivity for the page-server.
- `--relay-node edge-host-1` makes `host` mode use the third VM as the intermediate hop instead of the laptop.
- `direct` uses VM→VM `scp` and requires SSH trust; the orchestrator sets this up automatically (see `Network-live-migration/scripts/orchestrators/ssh_utils.py`).
- After a successful restore, the TCP client orchestrators write `/home/ubuntu/tcp_client.pid` (and a legacy alias `/home/ubuntu/client.pid`) on the destination, so you can “bounce” the client back and forth without re-running the workload launcher.

For the CSV schema, see `Network-live-migration/metrics/README.md`.
