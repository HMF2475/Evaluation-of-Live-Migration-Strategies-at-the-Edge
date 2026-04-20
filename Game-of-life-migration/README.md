# Game of Life / CRIU Migration Experiments

This directory contains a **Game of Life workload** plus a CRIU migration/benchmark pipeline modeled after the gol “holy grail” experiment:
- Native (non-container) CRIU migrations: **cold**, **pre-copy**, **post-copy (lazy-pages, experimental)**
- Repeatable benchmarking + metrics collection + plotting
- Optional node_exporter snapshots per run (CPU/memory/disk IO)

## What’s Where

- `Game-of-life-migration/scripts/setup/` — reset/cleanup, node_exporter install/checks, time sync checks, `tc` netem helper
  - See: `Game-of-life-migration/scripts/setup/README.md`
- `Game-of-life-migration/scripts/workloads/` — workload launchers
  - `start_game_of_life.sh` compiles + starts `/tmp/gol_service` and writes `/home/ubuntu/gol.pid`
  - See: `Game-of-life-migration/scripts/workloads/README.md`
- `Game-of-life-migration/scripts/orchestrators/` — benchmark runners (`criu_benchmark.py`, `repeat_benchmarks.py`)
  - See: `Game-of-life-migration/scripts/orchestrators/README.md`
- `Game-of-life-migration/metrics/` — CSV outputs, raw logs, node_exporter snapshots, plots
  - See: `Game-of-life-migration/metrics/README.md`
- `Game-of-life-migration/simplest-example/` — local (same-host) CRIU dump/restore sanity check

## Quick Start (repeatable benchmark batch)

For exhaustive end-to-end setup (Terraform/MultiPass, node_exporter, plots, all options), use:
- `GUIDE.md`

Once nodes exist and are ready, run a batch (example: 10 runs each strategy/mode):

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
