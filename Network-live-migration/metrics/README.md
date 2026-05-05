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

For TCP pre-copy, pre-dump image directories are archived, transferred, and unpacked while the TCP client is still running. The final dump references the last pre-dump directory, and restore needs the complete image chain already present on the destination. Therefore `archive_bytes`, `archive_create_ms`, `transfer_ms`, transfer detail columns, and `unpack_ms` describe only the final dump delta transferred during downtime. Earlier pre-dump archive/copy/unpack totals are recorded in `notes` as `precopy_stream_*` fields and are intentionally excluded from downtime plots.

## Plot Timing Convention

Generated plots subtract `transfer_setup_ms` from plotted `transfer_ms` and `downtime_ms`. The raw CSV remains unchanged. This treats SSH trust, IP lookup, source-file validation, and similar setup as pre-established deployment overhead rather than part of the migration window.

## Reading `transfer_phase_breakdown.png`

Each stacked bar is the mean setup-adjusted transfer phase for a migration method and transfer mode:

- `archive create`: compress/create the archive that will be moved. For TCP/CRIU this packages `/tmp/CRIU-tcp-client` into `CRIU-tcp-client.tar.gz`.
- `transfer setup`: stored in the CSV but omitted from generated transfer/downtime plots because setup is treated as pre-established deployment overhead.
- `copy` / `copy leg 1`: the file-copy operation. Direct mode shows a single `copy`; host mode shows `copy leg 1` from source VM to relay VM.
- `copy leg 2`: second file-copy operation, only for relay/host mode. It is omitted from the direct-mode legend when it is zero.
- `cleanup`: removal of temporary host files or relay-staged files. It is omitted from a mode legend when it is zero.
- `destination unpack`: extract the transferred archive on the destination before restore.

The small `+/-SD` labels inside or immediately above each segment show the standard deviation of that specific transfer sub-phase.

## Reading `phase_breakdown.png`

Each stacked bar is the mean checkpoint/final-dump, setup-adjusted transfer, and restore time for a method and transfer mode. The small `+/-SD` labels inside or immediately above each segment show the standard deviation of that specific phase.

## `node_exporter_metrics.csv` schema

See the header row in `node_exporter_metrics.csv` (it matches the scripts in `scripts/orchestrators/node_exporter_metrics.py`).
