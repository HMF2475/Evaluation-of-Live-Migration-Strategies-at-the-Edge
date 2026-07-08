#!/usr/bin/env python3
"""
Plot detailed transfer-phase breakdown for setup-adjusted transfer time.

This figure is intentionally apples-to-apples with the transfer bar in
phase_breakdown.png: it decomposes the plotted transfer phase, defined as the
window between checkpoint completion and restore start, excluding setup.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import (
    annotate_segment_std,
    apply_plot_theme,
    format_plain_axes,
    load_migration_csv,
    ordered_methods,
    ordered_transfer_modes,
    phase_colors,
    resolve_output_file,
    save_current_figure,
    successful_runs_only,
)


PHASE_COLUMNS = [
    ("archive_create_ms", "archive create"),
    ("transfer_send_ms", "copy leg 1"),
    ("transfer_receive_ms", "copy leg 2"),
    ("transfer_cleanup_ms", "cleanup"),
    ("unpack_ms", "destination unpack"),
]


RAW_DETAILED_COLUMNS = [
    "transfer_setup_ms",
    *[name for name, _ in PHASE_COLUMNS],
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

    df = successful_runs_only(df)
    if df.empty:
        print("No successful rows selected for transfer phase breakdown plot.")
        return

    detailed_available = [name for name in RAW_DETAILED_COLUMNS if name in df.columns]
    if not detailed_available:
        print("Skipping transfer phase breakdown: no detailed transfer columns found.")
        return

    available = [name for name, _ in PHASE_COLUMNS if name in df.columns]
    if not available:
        print(
            "Skipping transfer phase breakdown: no transfer copy/cleanup columns found."
        )
        return

    for column in detailed_available:
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
    phase_stds = (
        df.groupby(["migration_method", "transfer_mode"])[active_columns]
        .std()
        .reset_index()
    )
    phase_stds = phase_stds.fillna(0.0)

    methods = ordered_methods(phases["migration_method"].astype(str))
    modes = ordered_transfer_modes(phases["transfer_mode"].astype(str))

    output_file = resolve_output_file(output_file, "transfer_phase_breakdown.png")
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    apply_plot_theme()

    plt.figure(figsize=(9.0, 4.2))
    ax = plt.gca()
    x = np.arange(len(methods))
    width = 0.35 if len(modes) > 1 else 0.6
    labels = dict(PHASE_COLUMNS)
    legend_seen: set[str] = set()
    max_top = 0.0
    label_records = []
    color_labels = []
    for mode in modes:
        for column in active_columns:
            phase_label = labels[column]
            if mode == "direct" and column == "transfer_send_ms":
                phase_label = "copy"
            color_labels.append(f"{mode}: {phase_label}")
    colors = phase_colors(color_labels)

    for j, mode in enumerate(modes):
        sub = phases[phases["transfer_mode"] == mode].set_index("migration_method")
        std_sub = phase_stds[phase_stds["transfer_mode"] == mode].set_index(
            "migration_method"
        )
        bottom = np.zeros(len(methods))
        offset = (j - (len(modes) - 1) / 2) * width
        bar_x = x + offset
        for column in active_columns:
            values = np.array(
                [float(sub.loc[m, column]) if m in sub.index else 0.0 for m in methods]
            )
            if np.allclose(values, 0.0):
                continue

            phase_label = labels[column]
            if mode == "direct" and column == "transfer_send_ms":
                phase_label = "copy"
            legend_label = f"{mode}: {phase_label}"
            label = legend_label if legend_label not in legend_seen else "_nolegend_"
            legend_seen.add(legend_label)

            ax.bar(
                bar_x,
                values,
                width,
                bottom=bottom,
                label=label,
                color=colors[legend_label],
                edgecolor="white",
                linewidth=0.4,
            )
            std_values = np.array(
                [
                    float(std_sub.loc[m, column]) if m in std_sub.index else 0.0
                    for m in methods
                ]
            )
            for bx, base, height, std in zip(bar_x, bottom, values, std_values):
                if height <= 0:
                    continue
                max_top = max(max_top, float(base + height))
                label_records.append(
                    (float(bx), float(base), float(height), float(std))
                )
            bottom += values

    if max_top > 0:
        y_upper = max_top * 1.14
        ax.set_ylim(top=y_upper)
        for bx, base, height, std in label_records:
            annotate_segment_std(ax, bx, base, height, std, y_upper)
    ax.set_xlabel("Migration Method")
    ax.set_ylabel("Transfer time (ms)")
    format_plain_axes(ax, "y")
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=0)
    ax.text(
        0.01,
        0.98,
        "Segment labels show +/-SD (ms)",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=10,
        color="#333333",
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 2},
    )
    ax.legend(
        ncol=4,
        fontsize=9,
        frameon=False,
        bbox_to_anchor=(0.5, 1.08),
        loc="lower center",
    )
    plt.tight_layout(rect=[0.04, 0, 1, 0.84])
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

    plot_transfer_phase_breakdown(csv_path, output_path, title_suffix)
