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
        output_file: Output PNG filepath (defaults to WASM-migration/metrics/plots/downtime_comparison.png)
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
    plt.figure(figsize=(10, 6))
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
    ax = plt.gca()
    format_plain_axes(ax, "y")
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        unique = {}
        for h, lab in zip(handles, labels):
            if lab not in unique:
                unique[lab] = h
        ax.legend(
            list(unique.values()),
            list(unique.keys()),
            title="transfer_mode",
            bbox_to_anchor=(1.02, 1),
            loc="upper left",
            borderaxespad=0,
        )
    add_success_rate_note(ax, success_note, y=0.68)
    base_title = "Migration Downtime by Strategy (Host vs Direct)"
    full_title = f"{base_title} - {title_suffix}" if title_suffix else base_title
    plt.title(full_title)
    plt.ylabel("Downtime excl. transfer setup (ms)")
    plt.xlabel("Migration Method")
    plt.tight_layout(rect=[0, 0, 0.85, 1])
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

    plot_downtime(csv_path, output_path, title_suffix)
