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
    add_success_rate_note,
    annotate_segment_stds,
    apply_plot_theme,
    format_plain_axes,
    load_migration_csv,
    ordered_methods,
    ordered_transfer_modes,
    phase_colors,
    place_figure_legend,
    PLOT_FIGSIZE,
    resolve_output_file,
    save_current_figure,
    success_rate_note,
    successful_runs_only,
)


PHASE_COLUMNS = [
    ("archive_create_ms", "archive create"),
    ("transfer_send_ms", "copy leg 1"),
    ("transfer_receive_ms", "copy leg 2"),
    ("transfer_cleanup_ms", "cleanup"),
    ("unpack_ms", "destination unpack"),
]

NATO_PHASE_PALETTE = {
    "archive create": "#7FCDBB",
    "copy": "#41B6C4",
    "copy leg 2": "#1D91C0",
    "cleanup": "#225EA8",
    "destination unpack": "#253494",
}
SQUARE_FIGSIZE = (5.4, 5.4)


RAW_DETAILED_COLUMNS = [
    "transfer_setup_ms",
    *[name for name, _ in PHASE_COLUMNS],
]


def plot_transfer_phase_breakdown(
    csv_file: str,
    output_file: str = None,
    title_suffix: str = "",
    *,
    color_scheme: str = "default",
    show_run_status: bool = True,
    transfer_mode: str | None = None,
    square: bool = False,
) -> None:
    if not Path(csv_file).exists():
        print(f"ERROR: CSV file not found: {csv_file}")
        sys.exit(1)

    df = load_migration_csv(csv_file)
    if df.empty:
        print("ERROR: CSV file is empty")
        sys.exit(1)

    if transfer_mode:
        df = df[df["transfer_mode"].astype(str).eq(transfer_mode)].copy()
        if df.empty:
            print(f"No {transfer_mode} rows selected for transfer phase breakdown.")
            return

    success_note = success_rate_note(df)
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

    fig, ax = plt.subplots(figsize=SQUARE_FIGSIZE if square else PLOT_FIGSIZE)
    x = np.arange(len(methods))
    width = 0.35 if len(modes) > 1 else 0.6
    labels = dict(PHASE_COLUMNS)
    legend_seen: set[str] = set()
    max_top = 0.0
    label_records = []
    color_labels = [
        "copy" if column == "transfer_send_ms" else labels[column]
        for column in active_columns
    ]
    if color_scheme == "nato":
        colors = {label: NATO_PHASE_PALETTE[label] for label in color_labels}
    else:
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

            phase_label = "copy" if column == "transfer_send_ms" else labels[column]
            legend_label = phase_label
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
        annotate_segment_stds(
            ax,
            label_records,
            y_upper,
            fontsize_offset=2 if square else 0,
        )
    font_offset = 2 if square else 0
    ax.set_xlabel("Migration Method", fontsize=17 + font_offset)
    ax.set_ylabel("Transfer time (ms)", fontsize=17 + font_offset)
    format_plain_axes(ax, "y")
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=0)
    ax.tick_params(axis="both", labelsize=15 + font_offset)
    handles, legend_labels = ax.get_legend_handles_labels()
    legend_title = "Color: transfer subphase"
    if transfer_mode:
        legend_title += f"  |  {transfer_mode.title()} transfer mode"
    else:
        legend_title += "  |  Left bar: Host  |  Right bar: Direct"
    place_figure_legend(
        fig,
        handles,
        legend_labels,
        title=legend_title,
        ncol=5,
        fontsize=12 if square else 14,
        title_fontsize=13 if square else 15,
    )
    if show_run_status:
        figure_note = success_note + "\nSegment labels: +/-SD (ms)"
        add_success_rate_note(ax, figure_note)
        bottom = 0.25 + 0.05 * figure_note.count("\n")
    else:
        bottom = 0.18
    top = 0.76 if square else 0.66
    fig.subplots_adjust(
        left=0.16 if square else 0.11, right=0.98, bottom=bottom, top=top
    )
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
