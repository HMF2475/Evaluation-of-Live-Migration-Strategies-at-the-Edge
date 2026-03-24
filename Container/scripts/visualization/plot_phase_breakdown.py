#!/usr/bin/env python3
"""
Plot migration phase breakdown (stacked bar chart).

Generates a stacked bar chart showing the time spent in each phase
(checkpoint, transfer, restore) for different migration methods.
"""

import pandas as pd
import matplotlib.pyplot as plt
import sys
from pathlib import Path
import numpy as np


def plot_phase_breakdown(csv_file: str, output_file: str = None):
    """
    Create phase breakdown stacked bar chart.
    
    Args:
        csv_file: Path to migration metrics CSV
        output_file: Output PNG filepath (defaults to Container/metrics/plots/phase_breakdown.png)
    """
    if not Path(csv_file).exists():
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)
    
    df = pd.read_csv(csv_file)
    
    if df.empty:
        print("ERROR: CSV file is empty")
        sys.exit(1)
    
    # Default output path - calculate relative to this script's location
    if output_file is None:
        script_dir = Path(__file__).resolve().parent  # Container/scripts/visualization
        output_file = script_dir.parent.parent / "metrics" / "plots" / "phase_breakdown.png"
    
    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    # Group by migration method and calculate averages
    phases = df.groupby('migration_method')[['checkpoint_ms', 'transfer_ms', 'restore_ms']].mean()
    
    plt.figure(figsize=(12, 6))
    
    x = np.arange(len(phases.index))
    width = 0.6
    
    plt.bar(x, phases['checkpoint_ms'], width, label='Checkpoint')
    plt.bar(x, phases['transfer_ms'], width, bottom=phases['checkpoint_ms'], label='Transfer')
    plt.bar(x, phases['restore_ms'], width,
            bottom=phases['checkpoint_ms'] + phases['transfer_ms'], label='Restore')
    
    plt.xlabel('Migration Method')
    plt.ylabel('Time (ms)')
    plt.title('Migration Phase Breakdown (Average)')
    plt.xticks(x, phases.index, rotation=45)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_file, dpi=300)
    print(f"✓ Saved: {output_file}")
    plt.close()


if __name__ == "__main__":
    csv_path = "Container/metrics/migration_metrics.csv"
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    
    output_path = None
    if len(sys.argv) > 2:
        output_path = sys.argv[2]
    
    plot_phase_breakdown(csv_path, output_path)
