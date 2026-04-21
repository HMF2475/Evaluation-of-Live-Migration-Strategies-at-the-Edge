# Setup Scripts

Utilities for node cleanup, node_exporter checks, clock checks, and optional netem injection.

## Key Scripts

- `reset_nodes.py`: cleanup source, destination, and server nodes before a run.
- `install_node_exporter.sh`: install and enable node_exporter in each VM.
- `check_node_exporter_metrics.sh`: verify key metrics are exposed.
- `snapshot_node_exporter.sh`: capture one raw `/metrics` snapshot.
- `check_time_sync.sh`: print host-vs-node clock deltas.
- `apply_tc_netem.sh`: apply or clear delay/loss/rate on one interface.

## Typical Commands

```bash
# Full reset (recommended before each run)
python3 Network-live-migration/scripts/setup/reset_nodes.py edge-node-1 edge-node-2 edge-host-1

# node_exporter lifecycle
bash Network-live-migration/scripts/setup/install_node_exporter.sh edge-node-1 edge-node-2 edge-host-1
bash Network-live-migration/scripts/setup/check_node_exporter_metrics.sh edge-node-1 edge-node-2 edge-host-1

# Optional netem
bash Network-live-migration/scripts/setup/apply_tc_netem.sh edge-host-1 enp0s2 --delay 80ms --loss 1%
```

Use `Network-live-migration/TCP-live-migration.md` for the full experiment flow.
