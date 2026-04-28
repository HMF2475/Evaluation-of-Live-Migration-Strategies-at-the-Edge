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

from common import load_migration_csv, ordered_methods, resolve_output_file


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

    output_file = resolve_output_file(output_file, "downtime_comparison.png")

    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(10, 6))
    order = ordered_methods(df["migration_method"].astype(str))
    if not order:
        order = None
    sns.boxplot(
        data=df,
        x="migration_method",
        y="downtime_ms",
        hue="transfer_mode",
        order=order,
        showfliers=False,
    )
    sns.stripplot(
        data=df,
        x="migration_method",
        y="downtime_ms",
        hue="transfer_mode",
        order=order,
        dodge=True,
        alpha=0.35,
        size=3,
        linewidth=0,
    )
    handles, labels = plt.gca().get_legend_handles_labels()
    if handles:
        unique = {}
        for h, lab in zip(handles, labels):
            if lab not in unique:
                unique[lab] = h
        plt.legend(
            list(unique.values()),
            list(unique.keys()),
            title="transfer_mode",
            bbox_to_anchor=(1.02, 1),
            loc="upper left",
            borderaxespad=0,
        )
    base_title = "Migration Downtime by Strategy (Host vs Direct)"
    full_title = f"{base_title} - {title_suffix}" if title_suffix else base_title
    plt.title(full_title)
    plt.ylabel("Downtime (ms)")
    plt.xlabel("Migration Method")
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

    plot_downtime(csv_path, output_path, title_suffix)
