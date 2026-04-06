# Setup Scripts

## Overview

Node initialization and cleanup tools (reset, time sync checks, node_exporter install/snapshots, `tc` netem).

For end-to-end benchmark instructions, see `GUIDE.md`.

## Host Requirements

These scripts run on the host and call into the Multipass VMs, so the host must have:

- `multipass`
- `python3`
- `curl` (for node_exporter snapshots)

For the full repo prerequisites (Terraform, virtualization, plotting deps), see the repo root `README.md`.

## Key Scripts

- `reset_nodes.py` — prepares source/destination nodes for a fresh migration experiment (idempotent).
- `install_node_exporter.sh` — installs and enables Prometheus `node_exporter` on each node.
- `check_time_sync.sh` — reports host vs node clocks and NTP sync status.
- `snapshot_node_exporter.sh` — stores a raw `/metrics` snapshot from a node onto the host.
- `check_node_exporter_metrics.sh` — sanity-checks key node_exporter metrics exist.
- `apply_tc_netem.sh` — applies or clears delay/loss/rate constraints on a node interface.

## Common Usage

### Reset nodes (recommended before every run)

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
```

### Install node_exporter

```bash
bash Container/scripts/setup/install_node_exporter.sh edge-node-1 edge-node-2 edge-host-1
```

### Bootstrap the Kubernetes cluster

```bash
bash Container/scripts/setup/bootstrap_k8s_cluster.sh \
  edge-node-1 edge-node-2 edge-host-1
```

### Apply `tc` netem on the relay node

```bash
bash Container/scripts/setup/apply_tc_netem.sh \
  edge-host-1 enp0s2 --delay 80ms --loss 1%
```

### Snapshot node_exporter metrics

```bash
bash Container/scripts/setup/snapshot_node_exporter.sh \
  --node edge-node-1 \
  --out Container/metrics/node_exporter/edge-node-1.prom
```

## Notes

- Run `reset_nodes.py` before any manual CRIU steps too (it clears old dumps/archives and PID files).
