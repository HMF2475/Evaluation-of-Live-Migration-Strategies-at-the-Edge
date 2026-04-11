# Workloads

## Overview

Test applications used by the migration orchestrators.

For end-to-end benchmark instructions, see `GUIDE.md`.

## Key Scripts

- `start_counter_c.sh` — baseline counter workload (memory-only, easiest to validate).


## Common Usage

### Counter (baseline)

```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1
```

What it does:
- Builds `/tmp/counter` on the node.
- Starts it with stdout redirected to `/home/ubuntu/counter.out`.
- Writes PID files: `/home/ubuntu/counter.pid`, `/home/ubuntu/app.pid`.

