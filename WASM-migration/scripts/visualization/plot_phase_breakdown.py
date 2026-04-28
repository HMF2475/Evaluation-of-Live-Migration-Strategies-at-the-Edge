#!/usr/bin/env python3
"""
Plot migration phase breakdown (checkpoint + transfer + restore).

Generates a stacked bar chart showing mean time spent in each phase for
different migration methods and transfer modes (host vs direct).
"""

import matplotlib.pyplot as plt
import sys
from pathlib import Path
import numpy as np

from common import load_migration_csv, ordered_methods, resolve_output_file


def plot_phase_breakdown(
    csv_file: str, output_file: str = None, title_suffix: str = ""
):
    """
    Create phase breakdown stacked bar chart.

    Args:
        csv_file: Path to migration metrics CSV
        output_file: Output PNG filepath (defaults to WASM-migration/metrics/plots/phase_breakdown.png)
        title_suffix: Suffix for the chart title
    """
    if not Path(csv_file).exists():
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)

    df = load_migration_csv(csv_file)

    if df.empty:
        print("ERROR: CSV file is empty")
        sys.exit(1)

    output_file = resolve_output_file(output_file, "phase_breakdown.png")

    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    phases = (
        df.groupby(["migration_method", "transfer_mode"])[
            ["checkpoint_plot_ms", "transfer_ms", "restore_ms"]
        ]
        .mean()
        .reset_index()
    )

    methods = ordered_methods(phases["migration_method"].astype(str))
    if not methods:
        methods = sorted(phases["migration_method"].unique().tolist())
    modes = [
        m
        for m in ["host", "direct", "unknown"]
        if m in set(phases["transfer_mode"].astype(str))
    ]
    if not modes:
        modes = sorted(phases["transfer_mode"].unique().tolist())

    plt.figure(figsize=(12, 6))
    x = np.arange(len(methods))
    width = 0.35 if len(modes) > 1 else 0.6

    for j, mode in enumerate(modes):
        sub = phases[phases["transfer_mode"] == mode].set_index("migration_method")
        chk = [
            float(sub.loc[m, "checkpoint_plot_ms"]) if m in sub.index else 0.0
            for m in methods
        ]
        trn = [
            float(sub.loc[m, "transfer_ms"]) if m in sub.index else 0.0 for m in methods
        ]
        rst = [
            float(sub.loc[m, "restore_ms"]) if m in sub.index else 0.0 for m in methods
        ]

        offset = (j - (len(modes) - 1) / 2) * width
        plt.bar(x + offset, chk, width, label=f"{mode}: checkpoint")
        plt.bar(x + offset, trn, width, bottom=chk, label=f"{mode}: transfer")
        plt.bar(
            x + offset,
            rst,
            width,
            bottom=np.array(chk) + np.array(trn),
            label=f"{mode}: restore",
        )

    plt.xlabel("Migration Method")
    plt.ylabel("Time (ms)")
    base_title = "Migration Phase Breakdown (Mean)"
    full_title = f"{base_title} - {title_suffix}" if title_suffix else base_title
    plt.title(full_title)
    plt.xticks(x, methods, rotation=0)
    plt.legend(
        ncol=1,
        fontsize=8,
        frameon=False,
        bbox_to_anchor=(1.02, 1),
        loc="upper left",
    )
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

    plot_phase_breakdown(csv_path, output_path, title_suffix)
