# Workloads

## Overview

Test applications used by the migration orchestrators.

For end-to-end benchmark instructions, see `GUIDE.md`.

## Key Scripts

- `start_counter_c.sh` — baseline counter workload (memory-only, easiest to validate).
- `start_tcp_echo.sh` — TCP echo server (network migration experiments).
- `start_udp_echo.sh` — UDP echo server (network migration experiments).

## Common Usage

### Counter (baseline)

```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1
```

What it does:
- Builds `/tmp/counter` on the node.
- Starts it with stdout redirected to `/home/ubuntu/counter.out`.
- Writes PID files: `/home/ubuntu/counter.pid`, `/home/ubuntu/app.pid`.

### TCP echo (experimental)

```bash
bash Container/scripts/workloads/start_tcp_echo.sh edge-node-1 5000
```

### UDP echo (experimental)

```bash
bash Container/scripts/workloads/start_udp_echo.sh edge-node-1 5001
```

## Notes

- TCP/UDP migration is experimental in this repo. When testing it, use the orchestrator flags `--network-migration yes` and (usually required) `--ext-net-map SRC_IP:DST_IP`.
