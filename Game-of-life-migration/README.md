# Game of Life / CRIU Migration Experiments


This directory contains a **Game of Life workload** (C implementation) and a CRIU migration/benchmark pipeline modeled after the "holy grail" experiment:
- Native (non-container) CRIU migrations: **cold**, **pre-copy**, **post-copy (lazy-pages)**
- Repeatable benchmarking + metrics collection + plotting
- Optional node_exporter snapshots per run (CPU/memory/disk IO)

## What’s Where

- `Game-of-life-migration/scripts/setup/` — reset/cleanup, node_exporter install/checks, time sync checks, `tc` netem helper
  - See: `Game-of-life-migration/scripts/setup/README.md`
- `Game-of-life-migration/scripts/workloads/` — workload launchers
  - `start_gol_c.sh` compiles + starts `/tmp/gol` and writes `/home/ubuntu/gol.pid`
  - See: `Game-of-life-migration/scripts/workloads/README.md`
- `Game-of-life-migration/scripts/orchestrators/` — benchmark runners (`criu_benchmark.py`, `repeat_benchmarks.py`)
  - See: `Game-of-life-migration/scripts/orchestrators/README.md`
- `Game-of-life-migration/metrics/` — CSV outputs, raw logs, node_exporter snapshots, plots
  - See: `Game-of-life-migration/metrics/README.md`
- `Game-of-life-migration/simplest-example/` — local (same-host) CRIU dump/restore sanity check

## Quick Start (repeatable benchmark batch)

For shared setup, smoke runs, metrics, and plot locations, use:
- `GUIDE.md`

After `bash tools/terraform/check_bootstrap.sh` passes, run a batch (example: 10 runs each strategy/mode):

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


## About the Game of Life Workload

The Game of Life workload is a C program (`gol.c`) that simulates Conway's Game of Life, printing an evolving 50x20 grid to stdout every second. It is used to test CRIU's ability to checkpoint and restore a non-trivial, stateful application.

To launch the workload manually:

```bash
bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1
```

Output is written to `/home/ubuntu/gol.out` on the node. PID files: `/home/ubuntu/gol.pid`, `/home/ubuntu/app.pid`.
