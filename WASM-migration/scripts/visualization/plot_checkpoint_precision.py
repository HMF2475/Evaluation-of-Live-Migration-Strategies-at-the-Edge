#!/usr/bin/env python3
"""
Plot checkpoint duration in microseconds.

This keeps the sub-millisecond checkpoint phase readable instead of mixing it
with transfer/restore phases that are naturally shown in milliseconds.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns

from common import (
    apply_plot_theme,
    load_migration_csv,
    ordered_methods,
    ordered_transfer_modes,
    resolve_output_file,
    save_current_figure,
    successful_runs_only,
    transfer_mode_palette,
)


def plot_checkpoint_precision(
    csv_file: str, output_file: str = None, title_suffix: str = ""
):
    """
    Create checkpoint duration chart in microseconds.

    Args:
        csv_file: Path to migration metrics CSV
        output_file: Output PNG filepath
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
        print("No successful rows selected for checkpoint precision plot.")
        return

    output_file = resolve_output_file(output_file, "checkpoint_precision.png")
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    order = ordered_methods(df["migration_method"].astype(str))
    if not order:
        order = None
    hue_order = ordered_transfer_modes(df["transfer_mode"].astype(str))
    hue_palette = transfer_mode_palette(hue_order)

    apply_plot_theme()
    plt.figure(figsize=(8.4, 4.2))
    sns.boxplot(
        data=df,
        x="migration_method",
        y="checkpoint_plot_us",
        hue="transfer_mode",
        order=order,
        hue_order=hue_order,
        palette=hue_palette,
        showfliers=False,
    )
    sns.stripplot(
        data=df,
        x="migration_method",
        y="checkpoint_plot_us",
        hue="transfer_mode",
        order=order,
        hue_order=hue_order,
        palette=hue_palette,
        dodge=True,
        alpha=0.45,
        size=3,
        linewidth=0,
    )

    handles, labels = plt.gca().get_legend_handles_labels()
    if handles:
        unique = {}
        for handle, label in zip(handles, labels):
            if label not in unique:
                unique[label] = handle
        plt.legend(
            list(unique.values()),
            list(unique.keys()),
            title="transfer_mode",
            bbox_to_anchor=(0.5, 1.08),
            loc="lower center",
            ncol=max(1, len(unique)),
            borderaxespad=0,
        )

    plt.ylabel("Checkpoint Time (us)")
    plt.xlabel("Migration Method")
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

    plot_checkpoint_precision(csv_path, output_path, title_suffix)
