# Migration Metrics

This directory stores benchmark results for **native CRIU process migration** experiments for the Game of Life workload across different migration strategies (cold, pre-copy, post-copy).

## Results Storage

All benchmark results are appended to: `migration_metrics.csv`

This file is the single source of truth for all migration performance data.

Additional optional artifacts:
- `run_logs/` - Raw stdout/stderr logs from repeated experiment runs (see `repeat_benchmarks.py`)
- `run_logs/*.run_ids.txt` - Run-id lists for plot filtering (one run_id per line)
- `node_exporter/` - Optional node_exporter `/metrics` snapshots (before/after runs)
- `node_exporter_metrics.csv` - Per-run CPU/memory/disk summary derived from snapshots
- `plots/` - Generated PNG plots (optionally per-run prefix subfolders)

## CSV Schema

```csv
run_id,technology,migration_method,network_migration,checkpoint_ms,archive_bytes,transfer_ms,restore_ms,downtime_ms,bandwidth_mbps,src_arch,dst_arch,same_arch,success,notes,timestamp,profile_name,predump_ms,final_dump_ms,total_ms,lazy_pages_active_ms,lazy_pages_log_bytes,archive_create_ms,transfer_setup_ms,transfer_send_ms,transfer_receive_ms,transfer_cleanup_ms,unpack_ms
```

Columns:
- `run_id` - Unique identifier for this benchmark run (default scheme: `DD-MM-YYYY-(host|direct)-(cold|precopy|postcopy)-NNNN`)
- `technology` - CRIU
- `migration_method` - cold, precopy, postcopy
- `network_migration` - compatibility field kept in the schema for merged plotting workflows
- `checkpoint_ms` - Time to dump process (milliseconds). For precopy, this is the **final dump only** (when service freezes), not including pre-dumps
- `archive_bytes` - Size of the checkpoint archive transferred during the measured downtime window. For precopy, this is the final dump archive only; earlier pre-dump archives are summarized in `notes`
- `transfer_ms` - Time to transfer the downtime archive between nodes (milliseconds)
- `restore_ms` - Time to restore process on destination (milliseconds)
- `downtime_ms` - Total service downtime (checkpoint_ms + transfer_ms + restore_ms). For precopy, this excludes pre-dump time since the service was still running during pre-dumps
- `bandwidth_mbps` - Effective bandwidth utilization during transfer (archive_bytes × 8 / (transfer_ms × 1000))
- `src_arch` - Source node architecture (x86_64, arm64, etc.)
- `dst_arch` - Destination node architecture
- `same_arch` - true if source and destination have matching architecture
- `success` - true/false indicating if migration completed successfully
- `notes` - Anomalies, errors, observations, transfer mode, and precopy stream fields such as `precopy_stream_transfer_ms`
- `profile_name` - Optional network profile/label (used to group/compare runs in plots)
- `predump_ms` - Total pre-copy preparation time for pre-copy runs, including pre-dumps and pre-dump archive/transfer/unpack work; not counted as downtime
- `final_dump_ms` - Final freeze dump time for pre-copy/post-copy analysis
- `total_ms` - Best-effort end-to-end wall-clock run duration
- `lazy_pages_active_ms` - Post-copy lazy-pages active time
- `lazy_pages_log_bytes` - Size of the lazy-pages log
- `archive_create_ms` - Time to compress/create the downtime archive. For precopy, this is the final dump archive only
- `transfer_setup_ms` - SSH/multipass setup, trust checks, IP lookup, and source-file validation
- `transfer_send_ms` - First copy leg: source to destination, relay, or host
- `transfer_receive_ms` - Second copy leg: relay or host to destination
- `transfer_cleanup_ms` - Cleanup of temporary or staged transfer files
- `unpack_ms` - Time to extract the archive on the destination

## Collection Tools

The modularized benchmark framework (`Game-of-life-migration/scripts/orchestrators/`) handles metric collection automatically. See `Game-of-life-migration/scripts/orchestrators/README.md` for architecture overview.

### Migration Strategy Implementation

The framework uses modularized strategy classes:
- `ColdMigration` - Immediate checkpoint/restore (full downtime)
- `PrecopyMigration` - Iterative pre-dumps with final freeze (reduced downtime)
- `PostcopyMigration` - Lazy page transfer on demand (requires CRIU `lazy-pages` + userfaultfd)

## Analysis and Visualization

Use the provided visualization scripts or Seaborn, Pandas, and Matplotlib to analyze results.

**Using the visualization scripts**:
```bash
# Visualize downtime by migration method
python3 Game-of-life-migration/scripts/visualization/plot_downtime.py Game-of-life-migration/metrics/migration_metrics.csv

# Analyze archive size vs transfer time
python3 Game-of-life-migration/scripts/visualization/plot_transfer_analysis.py Game-of-life-migration/metrics/migration_metrics.csv

# See phase breakdown (checkpoint, transfer, restore)
python3 Game-of-life-migration/scripts/visualization/plot_phase_breakdown.py Game-of-life-migration/metrics/migration_metrics.csv

# See detailed archive/copy/unpack breakdown around transfer_ms
python3 Game-of-life-migration/scripts/visualization/plot_transfer_phase_breakdown.py Game-of-life-migration/metrics/migration_metrics.csv
```


## Interpreting Results

### Downtime Analysis

The critical metric is **downtime_ms** (checkpoint_ms + transfer_ms + restore_ms). This is the service unavailability window:

- **Cold:** Full offline window (freeze → transfer → restore)
  - downtime_ms = checkpoint_ms + transfer_ms + restore_ms
  
- **Pre-Copy:** Reduced offline window (final freeze + final-delta transfer + restore)
  - downtime_ms = **final_dump_ms** + transfer_ms + restore_ms
  - Pre-dumps and their image transfers occur while the service is still running (not counted as downtime)
  - The final dump references the last pre-dump directory, and restore needs the complete image chain. The benchmark therefore copies each pre-dump directory to the destination before the final freeze, then transfers only the final dump delta during downtime.
  
- **Post-Copy:** Minimal offline window (restore is quick, pages fetched on demand)
  - Implemented with CRIU `lazy-pages` (post-copy). Downtime covers dump-init + transfer + restore, while page fetching continues in the background.


### Transfer Overhead
Compare `archive_bytes` and `transfer_ms` to understand network efficiency:
- Smaller archives = less bandwidth
- Pre-copy may transfer data multiple times: pre-dump images before downtime, then the final dump delta during downtime
- Post-copy transfers minimal images first, then pages on-demand (lazy-pages)

For new runs, `transfer_phase_breakdown.png` decomposes the same setup-adjusted transfer phase used by the downtime and phase plots. It covers the window between checkpoint completion and restore start: archive creation, copy leg 1, copy leg 2, transfer cleanup, and destination unpack, while excluding transfer setup.

### Plot Timing Convention

Generated plots subtract `transfer_setup_ms` from plotted `transfer_ms` and `downtime_ms`. The raw CSV remains unchanged. This treats SSH trust, IP lookup, source-file validation, and similar setup as pre-established deployment overhead rather than part of the migration window.

## Reading `transfer_phase_breakdown.png`

Each stacked bar is the mean setup-adjusted transfer phase for a migration method and transfer mode:

- `archive create`: compress/create the archive that will be moved.
- `transfer setup`: stored in the CSV but omitted from generated transfer/downtime plots because setup is treated as pre-established deployment overhead.
- `copy` / `copy leg 1`: the file-copy operation. Direct mode shows a single `copy`; host mode shows `copy leg 1` from source VM to relay VM.
- `copy leg 2`: second file-copy operation, only for relay/host mode. It is omitted from the direct-mode legend when it is zero.
- `cleanup`: removal of temporary host files or relay-staged files. It is omitted from a mode legend when it is zero.
- `destination unpack`: extract the transferred archive on the destination before restore.

The small `+/-SD` labels inside or immediately above each segment show the standard deviation of that specific transfer sub-phase.

For pre-copy rows, the stacked transfer breakdown refers to the final dump delta transferred during downtime. Earlier pre-dump archive, copy, and unpack timings are stored in the `precopy_stream_*` fields inside `notes` and are intentionally excluded from downtime plots.

## Reading `phase_breakdown.png`

Each stacked bar is the mean checkpoint/final-dump, setup-adjusted transfer, and restore time for a method and transfer mode. The small `+/-SD` labels inside or immediately above each segment show the standard deviation of that specific phase.

### Architecture Compatibility
- `same_arch=true` - No architecture mismatch overhead
- `same_arch=false` - Verify results (some operations may fail if architectures differ)

## Data Quality Notes

- All times are in milliseconds
- Archive sizes are in bytes
- Bandwidth in Mbps (calculated as: archive_bytes × 8 / (transfer_ms × 1000))
- Failed runs are recorded with notes indicating failure reason
- Transfer mode (host/direct) is recorded in notes field for filtering
- Continuity verification logs report `Expected min` (minimum value immediately after restore) and `Observed` after a short wait, so `Observed` is typically higher
