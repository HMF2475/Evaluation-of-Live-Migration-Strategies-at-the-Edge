#!/usr/bin/env python3
"""
Plot migration phase breakdown (checkpoint + transfer + restore).

Generates a stacked bar chart showing mean time spent in each phase for
different migration methods and transfer modes (host vs direct).
"""

import pandas as pd
import matplotlib.pyplot as plt
import sys
from pathlib import Path
import numpy as np

from common import load_migration_csv, resolve_output_file


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
    
    df = load_migration_csv(csv_file)
    
    if df.empty:
        print("ERROR: CSV file is empty")
        sys.exit(1)
    
    output_file = resolve_output_file(output_file, "phase_breakdown.png")
    
    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    phases = (
        df.groupby(["migration_method", "transfer_mode"])[["checkpoint_ms", "transfer_ms", "restore_ms"]]
        .mean()
        .reset_index()
    )

    methods = [m for m in ["cold", "precopy", "postcopy"] if m in set(phases["migration_method"].astype(str))]
    if not methods:
        methods = sorted(phases["migration_method"].unique().tolist())
    modes = [m for m in ["host", "direct", "unknown"] if m in set(phases["transfer_mode"].astype(str))]
    if not modes:
        modes = sorted(phases["transfer_mode"].unique().tolist())

    plt.figure(figsize=(12, 6))
    x = np.arange(len(methods))
    width = 0.35 if len(modes) > 1 else 0.6

    for j, mode in enumerate(modes):
        sub = phases[phases["transfer_mode"] == mode].set_index("migration_method")
        chk = [float(sub.loc[m, "checkpoint_ms"]) if m in sub.index else 0.0 for m in methods]
        trn = [float(sub.loc[m, "transfer_ms"]) if m in sub.index else 0.0 for m in methods]
        rst = [float(sub.loc[m, "restore_ms"]) if m in sub.index else 0.0 for m in methods]

        offset = (j - (len(modes) - 1) / 2) * width
        plt.bar(x + offset, chk, width, label=f"{mode}: checkpoint")
        plt.bar(x + offset, trn, width, bottom=chk, label=f"{mode}: transfer")
        plt.bar(x + offset, rst, width, bottom=np.array(chk) + np.array(trn), label=f"{mode}: restore")

    plt.xlabel("Migration Method")
    plt.ylabel("Time (ms)")
    plt.title("Migration Phase Breakdown (Mean)")
    plt.xticks(x, methods, rotation=0)
    plt.legend(ncol=3, fontsize=8, frameon=False)
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
