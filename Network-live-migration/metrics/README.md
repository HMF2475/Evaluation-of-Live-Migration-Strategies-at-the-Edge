# Metrics

This folder stores the “holy grail” metrics for `Network-live-migration/`.

The main CSV keeps the common timing columns used by `Container/metrics/migration_metrics.csv`
and adds TCP-specific timing columns. Plotting scripts handle these shared columns directly.

## Files

- `migration_metrics.csv`: one row per migration run (common timing columns plus TCP extras)
- `node_exporter_metrics.csv`: one row per run with source/destination CPU/memory/disk deltas
- `node_exporter/`: raw `node_exporter` snapshots per run (only when snapshots are enabled)
- `run_logs/`: per-suite execution logs and run-id lists
- `plots/`: generated PNG figures

## `migration_metrics.csv` schema

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
- `profile_name`
- `predump_ms`
- `final_dump_ms`
- `total_ms`
- `lazy_pages_active_ms`
- `lazy_pages_log_bytes`
- `archive_create_ms`
- `transfer_setup_ms`
- `transfer_send_ms`
- `transfer_receive_ms`
- `transfer_cleanup_ms`
- `unpack_ms`

The `profile_name` column is filled when you run suite runners with `--profile-name` (for example via `run_all.py`).

The detailed transfer columns split archive creation, SSH/multipass setup, first/second copy legs, cleanup, and destination unpack. They feed `transfer_phase_breakdown.png`, which is useful for TCP runs because `transfer_ms` can otherwise mix network copy time with fixed control-plane and archive overhead.

## Reading `transfer_phase_breakdown.png`

Each stacked bar is the mean transfer-side cost for a migration method and transfer mode:

- `archive create`: compress/create the archive that will be moved. For TCP/CRIU this packages `/tmp/CRIU-tcp-client` into `CRIU-tcp-client.tar.gz`.
- `transfer setup`: orchestration before copying data, such as destination IP lookup, source-file validation, SSH trust setup, SSH test connection, or host temp-file creation.
- `copy leg 1`: first file-copy operation. In direct mode this is source VM to destination VM. In host mode this is source VM to relay VM.
- `copy leg 2`: second file-copy operation, only for relay/host mode. In host mode this is relay VM to destination VM. Direct mode normally has zero here.
- `cleanup`: removal of temporary host files or relay-staged files.
- `destination unpack`: extract the transferred archive on the destination before restore.

Use this plot to explain whether high `transfer_ms` comes from real data movement or from fixed overhead around the transfer. TCP migration is especially sensitive to setup and orchestration cost because the archive can be small while the fixed SCP/SSH/VIP handling overhead remains visible.

## `node_exporter_metrics.csv` schema

See the header row in `node_exporter_metrics.csv` (it matches the scripts in `scripts/orchestrators/node_exporter_metrics.py`).
