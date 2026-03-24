
# Visualization Scripts

Analysis and plotting tools for migration metrics.

## Scripts

### plot_downtime.py
Downtime comparison by migration strategy.

Usage:
  python3 plot_downtime.py [csv_file] [output_path]
  python3 plot_downtime.py Container/metrics/migration_metrics.csv
  python3 plot_downtime.py Container/metrics/migration_metrics.csv Container/metrics/plots/my_downtime.png

Default output: Container/metrics/plots/downtime_comparison.png

### plot_transfer_analysis.py
Archive size vs transfer time scatter plot.

Usage:
  python3 plot_transfer_analysis.py [csv_file] [output_path]

Default output: Container/metrics/plots/transfer_analysis.png

### plot_phase_breakdown.py
Phase breakdown stacked bar chart (checkpoint + transfer + restore).

Usage:
  python3 plot_phase_breakdown.py [csv_file] [output_path]

Default output: Container/metrics/plots/phase_breakdown.png

## Quick Start

Run all visualizations (saves to Container/metrics/plots/):

```bash
cd Container/scripts/visualization

python3 plot_downtime.py ../../metrics/migration_metrics.csv
python3 plot_transfer_analysis.py ../../metrics/migration_metrics.csv
python3 plot_phase_breakdown.py ../../metrics/migration_metrics.csv
```

Or from repo root:

```bash
python3 Container/scripts/visualization/plot_downtime.py
python3 Container/scripts/visualization/plot_transfer_analysis.py
python3 Container/scripts/visualization/plot_phase_breakdown.py
```

## Output

All plots are saved to: `Container/metrics/plots/`

Generated files:
  - downtime_comparison.png       (Bar chart)
  - transfer_analysis.png         (Scatter plot)
  - phase_breakdown.png           (Stacked bar chart)

High-resolution PNG (300 dpi).

## Requirements

```bash
pip3 install pandas seaborn matplotlib
```

## Notes

- All scripts assume CSV is in standard migration_metrics.csv format
- Output directory is created automatically if it doesn't exist
- Default paths can be overridden via command-line arguments
