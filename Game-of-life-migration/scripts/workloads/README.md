# Workloads

## Overview

Test applications used by the migration orchestrators.

For end-to-end benchmark instructions, see `GUIDE.md`.

## Key Scripts

- `start_gol_c.sh` — heap-backed Game of Life workload for CRIU benchmarks.


## Common Usage

### Gol (baseline)

```bash
bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1
```

What it does:
- Builds `/tmp/gol` on the node.
- Starts it with stdout redirected to `/home/ubuntu/gol.out`.
- Writes PID files: `/home/ubuntu/gol.pid`, `/home/ubuntu/app.pid`.

The default grid is `2048x2048`, using about 32 MiB of heap state across two
`uint32_t` grids. The program prints compact heartbeat lines with generation,
alive-cell count, and checksum instead of printing the full grid.

You can override the size for a manual run:

```bash
GOL_WIDTH=1024 GOL_HEIGHT=1024 bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1
```

For a visual demo, use a small grid and enable board rendering:

```bash
GOL_WIDTH=50 GOL_HEIGHT=20 GOL_OUTPUT_MODE=grid GOL_PATTERN=cannon bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1
```

Output modes:

```text
GOL_OUTPUT_MODE=summary   compact benchmark heartbeat, default
GOL_OUTPUT_MODE=grid      draw the board with X/- for small demo grids
```

Grid rendering falls back to summary if the grid is too large, so benchmark
runs do not accidentally fill `/home/ubuntu/gol.out`.

Initial patterns:

```text
GOL_PATTERN=random   deterministic pseudo-random board, default for benchmarks
GOL_PATTERN=cannon   centered Gosper glider gun for small visual demos
```

Approximate heap sizes:

```text
512x512     ≈ 2 MiB
1024x1024   ≈ 8 MiB
2048x2048   ≈ 32 MiB
4096x4096   ≈ 128 MiB
```
