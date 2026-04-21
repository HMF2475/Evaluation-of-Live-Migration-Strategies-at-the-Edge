# Workloads

## Overview

Test applications used by the migration orchestrators.

For end-to-end benchmark instructions, see `GUIDE.md`.

## Key Scripts

- `start_gol_c.sh` — baseline gol workload (memory-only, easiest to validate).


## Common Usage

### Gol (baseline)

```bash
bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1
```

What it does:
- Builds `/tmp/gol` on the node.
- Starts it with stdout redirected to `/home/ubuntu/gol.out`.
- Writes PID files: `/home/ubuntu/gol.pid`, `/home/ubuntu/app.pid`.

