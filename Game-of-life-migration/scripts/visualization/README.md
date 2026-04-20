# Visualization Scripts

## Overview

Plotting utilities for `Game-of-life-migration/metrics/migration_metrics.csv` and optional node_exporter snapshots captured by the repeat runners.

For the full benchmarking workflow, see `GUIDE.md`.

## Key Scripts

- `plot_downtime.py` — downtime comparison by strategy and transfer mode.
- `plot_phase_breakdown.py` — stacked phase breakdown (dump/final_dump + transfer + restore).
- `plot_transfer_analysis.py` — archive size vs transfer time scatter.
- `node_exporter_summary.py` — summarizes CPU/memory/disk IO from node_exporter snapshots.
- `generate_all_plots.py` — generates all plots into a single output directory and supports filtering.

## Common Usage

From repo root (recommended):

```bash
# Generate all plots for a specific batch (recommended filter)
python3 Game-of-life-migration/scripts/visualization/generate_all_plots.py \
  --csv Game-of-life-migration/metrics/migration_metrics.csv \
  --run-ids-file Game-of-life-migration/metrics/run_logs/<batch>.run_ids.txt \
  --out-dir Game-of-life-migration/metrics/plots/<batch>

# Single plots (optional)
python3 Game-of-life-migration/scripts/visualization/plot_downtime.py Game-of-life-migration/metrics/migration_metrics.csv
python3 Game-of-life-migration/scripts/visualization/plot_phase_breakdown.py Game-of-life-migration/metrics/migration_metrics.csv
python3 Game-of-life-migration/scripts/visualization/plot_transfer_analysis.py Game-of-life-migration/metrics/migration_metrics.csv
```

## Outputs

By default, plots are written under `Game-of-life-migration/metrics/plots/` (or your `--out-dir`).

## Requirements

```bash
pip3 install pandas seaborn matplotlib
```

## Notes

- Prefer filtering with `--run-ids-file` so plots compare only the runs you intend.
- node_exporter plots require `repeat_benchmarks.py --snapshot-node-metrics` (otherwise they are skipped).
