#!/usr/bin/env python3
"""
Plot archive size vs transfer time analysis (by method and transfer mode).

Generates a scatter plot showing the relationship between checkpoint archive
size and transfer duration, styled by transfer mode (host vs direct).
"""

import sys
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
import seaborn as sns

from common import (
    apply_plot_theme,
    format_plain_axes,
    load_migration_csv,
    migration_method_palette,
    ordered_methods,
    ordered_transfer_modes,
    resolve_output_file,
    save_current_figure,
    successful_runs_only,
)


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

    df = successful_runs_only(df)
    if df.empty:
        print("No successful rows selected for transfer analysis plot.")
        return

    df["archive_kib"] = df["archive_bytes"].astype(float) / 1024.0

    output_file = resolve_output_file(output_file, "transfer_analysis.png")
    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    apply_plot_theme()
    plt.figure(figsize=(8.4, 4.2))
    method_order = ordered_methods(df["migration_method"].astype(str))
    mode_order = ordered_transfer_modes(df["transfer_mode"].astype(str))
    sns.scatterplot(
        data=df,
        x="archive_kib",
        y="transfer_ms",
        hue="migration_method",
        hue_order=method_order,
        palette=migration_method_palette(method_order),
        style="transfer_mode",
        style_order=mode_order,
        s=85,
    )
    plt.xlabel("Archive Size (KiB)")
    plt.ylabel("Transfer Time excl. setup (ms)")
    format_plain_axes(plt.gca(), "x", "y")
    plt.legend(bbox_to_anchor=(0.5, 1.08), loc="lower center", ncol=3)
    plt.tight_layout(rect=[0, 0, 1, 0.88])
    save_current_figure(output_file)
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
