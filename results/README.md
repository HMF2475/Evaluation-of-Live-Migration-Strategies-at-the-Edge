# Results

This directory stores experiment output files.

## File Naming Convention

| Pattern | Description |
|---|---|
| `cold_migration_YYYYMMDD_HHMMSS.json` | Single cold migration run result |
| `precopy_migration_YYYYMMDD_HHMMSS.json` | Single pre-copy run result |
| `postcopy_migration_YYYYMMDD_HHMMSS.json` | Single post-copy run result |
| `hybrid_migration_YYYYMMDD_HHMMSS.json` | Single hybrid run result |
| `wasm_migration_YYYYMMDD_HHMMSS.json` | Single WASM migration run result |
| `runtime_compat_YYYYMMDD_HHMMSS.json` | WASM runtime compatibility check |
| `experiment_YYYYMMDD_HHMMSS.json` | Aggregated multi-run experiment output |
| `summary_YYYYMMDD_HHMMSS.csv` | CSV summary for spreadsheet/analysis |
| `bandwidth_*.json` | Network bandwidth monitoring data |
| `sys_monitor_*.json` | System resource monitoring data |

## Result JSON Schema

Each migration result file contains:

```json
{
  "migration_type": "cold|pre_copy|post_copy|hybrid|wasm",
  "timestamp": "YYYYMMDD_HHMMSS",
  "source_host": "...",
  "target_host": "...",
  "timings_ms": {
    "total_downtime": 0,
    "total_migration": 0
  },
  "data_transferred_mb": 0.0
}
```

Results are committed only if they contain meaningful benchmark data.
Generated result files from actual experiments should **not** be committed
to version control (see `.gitignore`).
