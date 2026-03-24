#!/usr/bin/env python3
"""
Plot archive size vs transfer time analysis.

Generates a scatter plot showing the relationship between checkpoint archive
size and network transfer duration.
"""

import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import sys
from pathlib import Path


def plot_transfer_analysis(csv_file: str, output_file: str = None):
    """
    Create transfer size vs time scatter plot.
    
    Args:
        csv_file: Path to migration metrics CSV
        output_file: Output PNG filepath (defaults to Container/metrics/plots/transfer_analysis.png)
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
        output_file = script_dir.parent.parent / "metrics" / "plots" / "transfer_analysis.png"
    
    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    plt.figure(figsize=(10, 6))
    sns.scatterplot(data=df, x='archive_bytes', y='transfer_ms', hue='migration_method', s=100)
    plt.title('Archive Size vs Transfer Time')
    plt.xlabel('Archive Size (bytes)')
    plt.ylabel('Transfer Time (ms)')
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
    
    plot_transfer_analysis(csv_path, output_path)
