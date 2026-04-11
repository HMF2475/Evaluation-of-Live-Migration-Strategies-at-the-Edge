# Metrics

This folder stores the “holy grail” metrics for `Network-live-migration/`.

The main CSV **matches the exact 16-column schema** used by the memory-only
benchmark in `Container/metrics/migration_metrics.csv`, so plots and analyses
can compare both experiments directly.

## Files

- `migration_metrics.csv`: one row per migration run (16-column compatible schema)
- `node_exporter_metrics.csv`: one row per run with source/destination CPU/memory/disk deltas
- `node_exporter/`: raw `node_exporter` snapshots per run (only when snapshots are enabled)
- `run_logs/`: per-suite execution logs and run-id lists
- `plots/`: generated PNG figures

## `migration_metrics.csv` schema (16 columns)

- `run_id`
- `technology`
- `migration_method` (`cold|precopy|postcopy`)
- `network_migration` (`yes|no`)
- `checkpoint_ms`
- `archive_bytes`
- `transfer_ms`
- `restore_ms`
- `downtime_ms`
- `bandwidth_mbps`
- `src_arch`
- `dst_arch`
- `same_arch`
- `success`
- `notes`
- `timestamp`

## `node_exporter_metrics.csv` schema

See the header row in `node_exporter_metrics.csv` (it matches the scripts in `scripts/orchestrators/node_exporter_metrics.py`).
