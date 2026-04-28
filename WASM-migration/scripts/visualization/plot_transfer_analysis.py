#!/usr/bin/env python3
"""
Plot archive size vs transfer time analysis (by method and transfer mode).

Generates a scatter plot showing the relationship between checkpoint archive
size and transfer duration, styled by transfer mode (host vs direct).
"""

import seaborn as sns
import matplotlib.pyplot as plt
import sys
from pathlib import Path

from common import load_migration_csv, resolve_output_file


def plot_transfer_analysis(
    csv_file: str, output_file: str = None, title_suffix: str = ""
):
    """
    Create transfer size vs time scatter plot.

    Args:
        csv_file: Path to migration metrics CSV
        output_file: Output PNG filepath (defaults to WASM-migration/metrics/plots/transfer_analysis.png)
        title_suffix: Suffix for the chart title
    """
    if not Path(csv_file).exists():
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)

    df = load_migration_csv(csv_file)

    if df.empty:
        print("ERROR: CSV file is empty")
        sys.exit(1)

    output_file = resolve_output_file(output_file, "transfer_analysis.png")

    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 6))
    sns.scatterplot(
        data=df,
        x="archive_bytes",
        y="transfer_ms",
        hue="migration_method",
        style="transfer_mode",
        s=85,
    )
    base_title = "Archive Size vs Transfer Time"
    full_title = f"{base_title} - {title_suffix}" if title_suffix else base_title
    plt.title(full_title)
    plt.xlabel("Archive Size (bytes)")
    plt.ylabel("Transfer Time (ms)")
    plt.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.tight_layout(rect=[0, 0, 0.85, 1])
    plt.savefig(output_file, dpi=300)
    print(f"✓ Saved: {output_file}")
    plt.close()


if __name__ == "__main__":
    csv_path = "WASM-migration/metrics/migration_metrics.csv"
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]

    output_path = None
    if len(sys.argv) > 2:
        output_path = sys.argv[2]

    title_suffix = ""
    if len(sys.argv) > 3:
        title_suffix = sys.argv[3]

    plot_transfer_analysis(csv_path, output_path, title_suffix)
