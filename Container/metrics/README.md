# Migration Metrics

This directory stores benchmark results for container service migration experiments across different migration strategies (cold, pre-copy, post-copy) and scenarios (memory-only, networked).

## Results Storage

All benchmark results are appended to: `migration_metrics.csv`

This file is the single source of truth for all migration performance data.

## CSV Schema

```csv
run_id,technology,migration_method,network_migration,checkpoint_ms,archive_bytes,transfer_ms,restore_ms,downtime_ms,bandwidth_mbps,src_arch,dst_arch,same_arch,success,notes,timestamp
```

Columns:
- `run_id` - Unique identifier for this benchmark run
- `technology` - CRIU
- `migration_method` - cold, precopy, postcopy, hybrid
- `network_migration` - yes (with TCP/UDP socket preservation), no (memory-only)
- `checkpoint_ms` - Time to dump/checkpoint process (milliseconds)
- `archive_bytes` - Size of the checkpoint archive transferred
- `transfer_ms` - Time to transfer archive between nodes (milliseconds)
- `restore_ms` - Time to restore process on destination (milliseconds)
- `downtime_ms` - Total service downtime (checkpoint + transfer + restore)
- `bandwidth_mbps` - Effective bandwidth utilization during transfer (archive_bytes × 8 / (transfer_ms × 1000))
- `src_arch` - Source node architecture (x86_64, arm64, etc.)
- `dst_arch` - Destination node architecture
- `same_arch` - true if source and destination have matching architecture
- `success` - true/false indicating if migration completed successfully
- `notes` - Any anomalies, errors, or observations

## Collection Tools

Use the Python benchmarking tool to automatically collect metrics:

```bash
# Cold migration
python3 Container/scripts/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id cold-run-1

# Pre-copy live migration
python3 Container/scripts/criu_benchmark.py precopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id precopy-run-1 \
  --iterations 2

# Post-copy lazy migration
python3 Container/scripts/criu_benchmark.py postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id postcopy-run-1
```

Results are automatically appended to `migration_metrics.csv`.

## Analysis and Visualization

Use Seaborn, Pandas, and Matplotlib to analyze results:

```bash
python3 Container/scripts/analyze_metrics.py
```

Or manually in Python:

```python
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Load metrics
df = pd.read_csv('migration_metrics.csv')

# Summary statistics by migration method
print(df.groupby('migration_method')[['checkpoint_ms', 'transfer_ms', 'restore_ms', 'downtime_ms']].describe())

# Visualize downtime comparison
sns.barplot(data=df, x='migration_method', y='downtime_ms')
plt.title('Migration Downtime by Strategy')
plt.ylabel('Downtime (ms)')
plt.show()
```

## Comparison Matrix

| Strategy | Scenario | Avg Downtime (ms) | Use Case | Status |
|---|---|---:|---|---|
| Cold |  |  |  |  |
| Pre-Copy |  |  |  |  |
| Post-Copy |  |  |  |  |
| Cold + Network |  |  |  |  |
| Pre-Copy + Network |  |  | |  |
| Post-Copy + Network |  |  | |  |
| WASM |  |  |  |  |

## Interpreting Results

### Downtime Analysis
The critical metric is **downtime_ms** (checkpoint + transfer + restore). This is the service unavailability window:
- **Cold:** All time spent offline
- **Pre-Copy:** Reduced offline window (only final dump + restore)
- **Post-Copy:** Minimal offline window (restore is quick, pages fetched on demand)

### Transfer Overhead
Compare `archive_bytes` and `transfer_ms` to understand network efficiency:
- Smaller archives = less bandwidth
- Pre-copy may transfer data multiple times (pre-dumps + final dump)
- Post-copy transfers full state at once

### Architecture Compatibility
- `same_arch=true` - No architecture mismatch overhead
- `same_arch=false` - Verify results (some operations may fail if architectures differ)

## Data Quality Notes

- All times are in milliseconds
- Archive sizes are in bytes
- Bandwidth in Mbps (calculated as: archive_bytes × 8 / (transfer_ms × 1000))
- Failed runs are recorded with notes indicating failure reason