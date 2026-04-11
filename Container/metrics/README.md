# Migration Metrics

This directory stores benchmark results for container service migration experiments across different migration strategies (cold, pre-copy, post-copy).

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
run_id,technology,migration_method,network_migration,checkpoint_ms,archive_bytes,transfer_ms,restore_ms,downtime_ms,bandwidth_mbps,src_arch,dst_arch,same_arch,success,notes,timestamp
```

Columns:
- `run_id` - Unique identifier for this benchmark run (default scheme: `DD-MM-YYYY-(host|direct)-(cold|precopy|postcopy)-NNNN`)
- `technology` - CRIU
- `migration_method` - cold, precopy, postcopy
- `network_migration` - compatibility field kept in the schema for merged plotting workflows
- `checkpoint_ms` - Time to dump process (milliseconds). For precopy, this is the **final dump only** (when service freezes), not including pre-dumps
- `archive_bytes` - Size of the checkpoint archive transferred
- `transfer_ms` - Time to transfer archive between nodes (milliseconds)
- `restore_ms` - Time to restore process on destination (milliseconds)
- `downtime_ms` - Total service downtime (checkpoint_ms + transfer_ms + restore_ms). For precopy, this excludes pre-dump time since the service was still running during pre-dumps
- `bandwidth_mbps` - Effective bandwidth utilization during transfer (archive_bytes × 8 / (transfer_ms × 1000))
- `src_arch` - Source node architecture (x86_64, arm64, etc.)
- `dst_arch` - Destination node architecture
- `same_arch` - true if source and destination have matching architecture
- `success` - true/false indicating if migration completed successfully
- `notes` - Anomalies, errors, observations, or transfer mode (e.g., `transfer_mode=direct`)

## Collection Tools

The modularized benchmark framework (`Container/scripts/orchestrators/`) handles metric collection automatically. See `Container/scripts/orchestrators/README.md` for architecture overview.

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
python3 Container/scripts/visualization/plot_downtime.py

# Analyze archive size vs transfer time
python3 Container/scripts/visualization/plot_transfer_analysis.py

# See phase breakdown (checkpoint, transfer, restore)
python3 Container/scripts/visualization/plot_phase_breakdown.py
```


## Interpreting Results

### Downtime Analysis

The critical metric is **downtime_ms** (checkpoint_ms + transfer_ms + restore_ms). This is the service unavailability window:

- **Cold:** Full offline window (freeze → transfer → restore)
  - downtime_ms = checkpoint_ms + transfer_ms + restore_ms
  
- **Pre-Copy:** Reduced offline window (only final freeze + restore)
  - downtime_ms = **final_dump_ms** + transfer_ms + restore_ms
  - Pre-dumps occur while service is still running (not counted as downtime)
  
- **Post-Copy:** Minimal offline window (restore is quick, pages fetched on demand)
  - Implemented as **experimental** CRIU `lazy-pages` (post-copy). Downtime covers dump-init + transfer + restore, while page fetching continues in the background.


### Transfer Overhead
Compare `archive_bytes` and `transfer_ms` to understand network efficiency:
- Smaller archives = less bandwidth
- Pre-copy may transfer data multiple times (pre-dumps + final dump)
- Post-copy transfers minimal images first, then pages on-demand (lazy-pages)

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
