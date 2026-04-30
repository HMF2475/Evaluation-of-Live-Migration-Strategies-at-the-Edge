# Visualization Scripts

## Overview

Plotting utilities for `WASM-migration/metrics/migration_metrics.csv` and
optional node_exporter snapshots captured by the WASM repeat runner.

## Key Scripts

- `plot_downtime.py` — downtime comparison by strategy and transfer mode.
- `plot_phase_breakdown.py` — stacked phase breakdown (dump/final_dump + transfer + restore).
- `plot_checkpoint_precision.py` — checkpoint duration in microseconds.
- `plot_transfer_analysis.py` — archive size vs transfer time scatter.
- `plot_transfer_phase_breakdown.py` — detailed archive/transfer/unpack breakdown for explaining what dominates `transfer_ms`.
- `node_exporter_summary.py` — summarizes CPU/memory/disk IO from node_exporter snapshots.
- `generate_all_plots.py` — generates all plots into a single output directory and supports filtering.

## Common Usage

From repo root (recommended):

```bash
# Generate all plots for a specific batch (recommended filter)
python3 WASM-migration/scripts/visualization/generate_all_plots.py \
  --csv WASM-migration/metrics/migration_metrics.csv \
  --run-ids-file WASM-migration/metrics/run_logs/<batch>.run_ids.txt \
  --out-dir WASM-migration/metrics/plots/<batch>

# Single plots (optional)
python3 WASM-migration/scripts/visualization/plot_downtime.py WASM-migration/metrics/migration_metrics.csv
python3 WASM-migration/scripts/visualization/plot_phase_breakdown.py WASM-migration/metrics/migration_metrics.csv
python3 WASM-migration/scripts/visualization/plot_checkpoint_precision.py WASM-migration/metrics/migration_metrics.csv
python3 WASM-migration/scripts/visualization/plot_transfer_analysis.py WASM-migration/metrics/migration_metrics.csv
python3 WASM-migration/scripts/visualization/plot_transfer_phase_breakdown.py WASM-migration/metrics/migration_metrics.csv
```

## Outputs

By default, plots are written under `WASM-migration/metrics/plots/` (or your `--out-dir`).

`generate_all_plots.py` writes:
- `downtime_comparison.png`
- `phase_breakdown.png`
- `checkpoint_precision.png`
- `transfer_analysis.png`
- `transfer_phase_breakdown.png` when detailed transfer timing columns exist
- `node_exporter_summary.png` and `node_exporter_by_node.png` when snapshots exist

## Requirements

```bash
pip3 install pandas seaborn matplotlib
```

## Notes

- Prefer filtering with `--run-ids-file` so plots compare only the runs you intend.
- `transfer_phase_breakdown.png` needs new CSV columns (`archive_create_ms`, `transfer_setup_ms`, `transfer_send_ms`, `transfer_receive_ms`, `transfer_cleanup_ms`, `unpack_ms`). Older CSVs are still valid; this plot is skipped when the detailed columns are absent or empty.
- node_exporter plots require `repeat_wasm_benchmarks.py --snapshot-node-metrics` (otherwise they are skipped).
