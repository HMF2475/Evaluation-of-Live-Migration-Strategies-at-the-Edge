# Setup Scripts

## Overview

Node initialization and cleanup tools (reset, time sync checks, node_exporter install/snapshots).

For end-to-end benchmark instructions, see `GUIDE.md`.

## Key Scripts

- `reset_nodes.py` — prepares both nodes for a fresh migration experiment (idempotent).
- `install_node_exporter.sh` — installs and enables Prometheus `node_exporter` on each node.
- `check_time_sync.sh` — reports host vs node clocks and NTP sync status.
- `snapshot_node_exporter.sh` — stores a raw `/metrics` snapshot from a node onto the host.
- `check_node_exporter_metrics.sh` — sanity-checks key node_exporter metrics exist.

## Common Usage

### Reset nodes (recommended before every run)

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
```

### Install node_exporter

```bash
bash Container/scripts/setup/install_node_exporter.sh edge-node-1 edge-node-2
```

### Snapshot node_exporter metrics

```bash
bash Container/scripts/setup/snapshot_node_exporter.sh \
  --node edge-node-1 \
  --out Container/metrics/node_exporter/edge-node-1.prom
```

## Notes

- Run `reset_nodes.py` before any manual CRIU steps too (it clears old dumps/archives and PID files).
