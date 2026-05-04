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

The benchmarked Game of Life workload is a C program (`scripts/workloads/gol.c`) that simulates Conway's Game of Life with two heap-allocated grids. By default it uses a `2048x2048` grid, which is about 32 MiB of heap state (`width * height * 2 grids * 4 bytes`). This makes the CRIU image large enough to study checkpoint/transfer/restore behavior, especially post-copy lazy-pages.

By default the process prints a compact heartbeat once per generation instead of the full board:

```text
generation=12 width=2048 height=2048 alive=... checksum=...
```

That keeps `/home/ubuntu/gol.out` useful for restore validation without making stdout dominate the benchmark.

To launch the workload manually:

```bash
bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1
```

Output is written to `/home/ubuntu/gol.out` on the node. PID files: `/home/ubuntu/gol.pid`, `/home/ubuntu/app.pid`.

The grid can be changed without recompiling by setting environment variables before launching:

```bash
GOL_WIDTH=1024 GOL_HEIGHT=1024 bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1
```

For a presentation/demo, switch back to drawing mode and use a small grid:

```bash
GOL_WIDTH=50 GOL_HEIGHT=20 GOL_OUTPUT_MODE=grid GOL_PATTERN=cannon bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1
```

Output modes:

```text
GOL_OUTPUT_MODE=summary   compact benchmark heartbeat, default
GOL_OUTPUT_MODE=grid      draw the board with X/- for small demo grids
```

Initial patterns:

```text
GOL_PATTERN=random   deterministic pseudo-random board, default for benchmarks
GOL_PATTERN=cannon   centered Gosper glider gun for small visual demos
```

Grid rendering is automatically disabled for very large grids to avoid flooding stdout.

Useful sizes:

```text
512x512     ≈ 2 MiB heap
1024x1024   ≈ 8 MiB heap
2048x2048   ≈ 32 MiB heap
4096x4096   ≈ 128 MiB heap
```
