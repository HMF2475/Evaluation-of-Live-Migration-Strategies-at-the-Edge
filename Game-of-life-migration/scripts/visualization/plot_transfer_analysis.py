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

from common import (
    add_success_rate_note,
    apply_plot_theme,
    format_plain_axes,
    load_migration_csv,
    migration_method_palette,
    ordered_methods,
    ordered_transfer_modes,
    place_figure_legend,
    PLOT_FIGSIZE,
    resolve_output_file,
    save_current_figure,
    success_rate_note,
    successful_runs_only,
)


def plot_transfer_analysis(
    csv_file: str, output_file: str = None, title_suffix: str = ""
):
    """
    Create transfer size vs time scatter plot.

    Args:
        csv_file: Path to migration metrics CSV
        output_file: Output PNG filepath (defaults to Game-of-life-migration/metrics/plots/transfer_analysis.png)
        title_suffix: Suffix for the chart title
    """
    if not Path(csv_file).exists():
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)

    df = load_migration_csv(csv_file)

    if df.empty:
        print("ERROR: CSV file is empty")
        sys.exit(1)

    success_note = success_rate_note(df)
    df = successful_runs_only(df)
    if df.empty:
        print("No successful rows selected for transfer analysis plot.")
        return

    df["archive_kib"] = df["archive_bytes"].astype(float) / 1024.0

    output_file = resolve_output_file(output_file, "transfer_analysis.png")

    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    apply_plot_theme()
    fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
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
        ax=ax,
    )
    ax.set_xlabel("Archive Size (KiB)")
    ax.set_ylabel("Transfer time (ms)")
    format_plain_axes(ax, "x", "y")
    handles, labels = ax.get_legend_handles_labels()
    filtered = [
        (handle, label)
        for handle, label in zip(handles, labels)
        if label not in {"migration_method", "transfer_mode"}
    ]
    if ax.legend_ is not None:
        ax.legend_.remove()
    display_labels = [
        label.replace("migration_method", "Method")
        .replace("transfer_mode", "Transfer mode")
        .replace("precopy", "Pre-copy")
        .replace("postcopy", "Post-copy")
        .replace("cold", "Cold")
        .replace("host", "Host")
        .replace("direct", "Direct")
        for _, label in filtered
    ]
    place_figure_legend(
        fig,
        [handle for handle, _ in filtered],
        display_labels,
        title="Color: migration method  |  Marker: transfer mode",
        ncol=max(len(method_order), len(mode_order), 1),
    )
    add_success_rate_note(ax, success_note)
    status_height = 0.23 + 0.05 * success_note.count("\n")
    fig.subplots_adjust(left=0.11, right=0.98, bottom=status_height, top=0.69)
    save_current_figure(output_file)
    print(f"✓ Saved: {output_file}")
    plt.close()


if __name__ == "__main__":
    csv_path = "Game-of-life-migration/metrics/migration_metrics.csv"
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]

    output_path = None
    if len(sys.argv) > 2:
        output_path = sys.argv[2]

    title_suffix = ""
    if len(sys.argv) > 3:
        title_suffix = sys.argv[3]

    plot_transfer_analysis(csv_path, output_path, title_suffix)
