#!/usr/bin/env python3
"""
Plot detailed transfer-phase breakdown.

This breaks the old `transfer_ms` wall time into the measurable parts around it:
archive compression, transfer setup, copy leg(s), cleanup, and destination unpack.
Older CSVs do not have these columns, so the plot is skipped for those rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import load_migration_csv, ordered_methods, resolve_output_file


PHASE_COLUMNS = [
    ("archive_create_ms", "archive create"),
    ("transfer_setup_ms", "transfer setup"),
    ("transfer_send_ms", "copy leg 1"),
    ("transfer_receive_ms", "copy leg 2"),
    ("transfer_cleanup_ms", "cleanup"),
    ("unpack_ms", "destination unpack"),
]


def plot_transfer_phase_breakdown(
    csv_file: str, output_file: str = None, title_suffix: str = ""
) -> None:
    if not Path(csv_file).exists():
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)

    df = load_migration_csv(csv_file)
    if df.empty:
        print("ERROR: CSV file is empty")
        sys.exit(1)

    available = [name for name, _ in PHASE_COLUMNS if name in df.columns]
    if not available:
        print("Skipping transfer phase breakdown: no detailed transfer columns found.")
        return

    for column in available:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)

    active_columns = [column for column in available if df[column].sum() > 0]
    if not active_columns:
        print("Skipping transfer phase breakdown: detailed transfer columns are empty.")
        return

    phases = (
        df.groupby(["migration_method", "transfer_mode"])[active_columns]
        .mean()
        .reset_index()
    )

    methods = ordered_methods(phases["migration_method"].astype(str))
    modes = [
        m
        for m in ["host", "direct", "unknown"]
        if m in set(phases["transfer_mode"].astype(str))
    ] or sorted(phases["transfer_mode"].astype(str).unique().tolist())

    output_file = resolve_output_file(output_file, "transfer_phase_breakdown.png")
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(13, 6))
    x = np.arange(len(methods))
    width = 0.35 if len(modes) > 1 else 0.6
    labels = dict(PHASE_COLUMNS)

    for j, mode in enumerate(modes):
        sub = phases[phases["transfer_mode"] == mode].set_index("migration_method")
        bottom = np.zeros(len(methods))
        offset = (j - (len(modes) - 1) / 2) * width
        for column in active_columns:
            values = np.array(
                [float(sub.loc[m, column]) if m in sub.index else 0.0 for m in methods]
            )
            plt.bar(
                x + offset,
                values,
                width,
                bottom=bottom,
                label=f"{mode}: {labels[column]}",
            )
            bottom += values

    plt.xlabel("Migration Method")
    plt.ylabel("Time (ms)")
    base_title = "Transfer Phase Breakdown (Mean)"
    plt.title(f"{base_title} - {title_suffix}" if title_suffix else base_title)
    plt.xticks(x, methods, rotation=0)
    plt.legend(
        ncol=1,
        fontsize=8,
        frameon=False,
        bbox_to_anchor=(1.02, 1),
        loc="upper left",
    )
    plt.tight_layout(rect=[0, 0, 0.82, 1])
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

    plot_transfer_phase_breakdown(csv_path, output_path, title_suffix)
