#!/usr/bin/env python3
"""
Plot migration downtime comparison by strategy and transfer mode.

Generates a distribution plot comparing downtime across migration methods
and transfer modes (host vs direct).
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
    ordered_methods,
    ordered_transfer_modes,
    place_figure_legend,
    PLOT_FIGSIZE,
    resolve_output_file,
    save_current_figure,
    success_rate_note,
    successful_runs_only,
    transfer_mode_palette,
)


def plot_downtime(csv_file: str, output_file: str = None, title_suffix: str = ""):
    """
    Create downtime comparison chart.

    Args:
        csv_file: Path to migration metrics CSV
        output_file: Output PNG filepath (defaults to Container/metrics/plots/downtime_comparison.png)
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
        print("No successful rows selected for downtime plot.")
        return

    output_file = resolve_output_file(output_file, "downtime_comparison.png")

    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    apply_plot_theme()
    fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
    order = ordered_methods(df["migration_method"].astype(str))
    if not order:
        order = None
    hue_order = ordered_transfer_modes(df["transfer_mode"].astype(str))
    hue_palette = transfer_mode_palette(hue_order)
    sns.boxplot(
        data=df,
        x="migration_method",
        y="downtime_ms",
        hue="transfer_mode",
        order=order,
        hue_order=hue_order,
        palette=hue_palette,
        showfliers=False,
    )
    sns.stripplot(
        data=df,
        x="migration_method",
        y="downtime_ms",
        hue="transfer_mode",
        order=order,
        hue_order=hue_order,
        palette=hue_palette,
        dodge=True,
        alpha=0.35,
        size=3,
        linewidth=0,
    )
    format_plain_axes(ax, "y")
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        unique = {}
        for h, lab in zip(handles, labels):
            if lab not in unique:
                unique[lab] = h
        if ax.legend_ is not None:
            ax.legend_.remove()
        place_figure_legend(
            fig,
            list(unique.values()),
            [label.title() for label in unique],
            title="Transfer mode",
            ncol=max(1, len(unique)),
        )
    add_success_rate_note(ax, success_note)
    plt.ylabel("Downtime (ms)")
    plt.xlabel("Migration Method")
    status_height = 0.23 + 0.05 * success_note.count("\n")
    fig.subplots_adjust(left=0.11, right=0.98, bottom=status_height, top=0.76)
    save_current_figure(output_file)
    print(f"✓ Saved: {output_file}")
    plt.close()


if __name__ == "__main__":
    csv_path = "Container/metrics/migration_metrics.csv"
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]

    output_path = None
    if len(sys.argv) > 2:
        output_path = sys.argv[2]

    title_suffix = ""
    if len(sys.argv) > 3:
        title_suffix = sys.argv[3]

    plot_downtime(csv_path, output_path, title_suffix)
