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

from common import (
    annotate_segment_stds,
    add_success_rate_note,
    apply_plot_theme,
    format_plain_axes,
    load_migration_csv,
    ordered_methods,
    ordered_transfer_modes,
    phase_colors,
    resolve_output_file,
    save_current_figure,
    success_rate_note,
    successful_runs_only,
)


def plot_phase_breakdown(
    csv_file: str, output_file: str = None, title_suffix: str = ""
):
    """
    Create phase breakdown stacked bar chart.

    Args:
        csv_file: Path to migration metrics CSV
        output_file: Output PNG filepath (defaults to Network-live-migration/metrics/plots/phase_breakdown.png)
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
        print("No successful rows selected for phase breakdown plot.")
        return

    output_file = resolve_output_file(output_file, "phase_breakdown.png")

    # Ensure output directory exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    apply_plot_theme()

    checkpoint_col = (
        "checkpoint_plot_ms" if "checkpoint_plot_ms" in df.columns else "checkpoint_ms"
    )
    phase_columns = [
        (checkpoint_col, "checkpoint"),
        ("transfer_ms", "transfer (excl. setup)"),
        ("restore_ms", "restore"),
    ]
    df["phase_total_ms"] = (
        df[checkpoint_col].astype(float)
        + df["transfer_ms"].astype(float)
        + df["restore_ms"].astype(float)
    )
    phases = (
        df.groupby(["migration_method", "transfer_mode"])[
            [column for column, _ in phase_columns]
        ]
        .mean()
        .reset_index()
    )
    phase_stds = (
        df.groupby(["migration_method", "transfer_mode"])[
            [column for column, _ in phase_columns]
        ]
        .std()
        .reset_index()
    )
    phase_stds = phase_stds.fillna(0.0)

    methods = ordered_methods(phases["migration_method"].astype(str))
    if not methods:
        methods = sorted(phases["migration_method"].unique().tolist())
    modes = ordered_transfer_modes(phases["transfer_mode"].astype(str))

    plt.figure(figsize=(8.8, 4.4))
    ax = plt.gca()
    x = np.arange(len(methods))
    width = 0.35 if len(modes) > 1 else 0.6
    max_top = 0.0
    label_records = []
    color_labels = [f"{mode}: {label}" for mode in modes for _, label in phase_columns]
    colors = phase_colors(color_labels)

    for j, mode in enumerate(modes):
        sub = phases[phases["transfer_mode"] == mode].set_index("migration_method")
        std_sub = phase_stds[phase_stds["transfer_mode"] == mode].set_index(
            "migration_method"
        )
        offset = (j - (len(modes) - 1) / 2) * width
        bar_x = x + offset
        bottom = np.zeros(len(methods))
        for column, phase_label in phase_columns:
            values = np.array(
                [float(sub.loc[m, column]) if m in sub.index else 0.0 for m in methods]
            )
            std_values = np.array(
                [
                    float(std_sub.loc[m, column]) if m in std_sub.index else 0.0
                    for m in methods
                ]
            )
            legend_label = f"{mode}: {phase_label}"
            ax.bar(
                bar_x,
                values,
                width,
                bottom=bottom,
                label=legend_label,
                color=colors[legend_label],
                edgecolor="white",
                linewidth=0.4,
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
        annotate_segment_stds(ax, label_records, y_upper)
    ax.set_xlabel("Migration Method")
    ax.set_ylabel("Time (ms)")
    format_plain_axes(ax, "y")
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=0)
    ax.text(
        0.01,
        0.98,
        "Visible labels show +/-SD (ms)",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8,
        color="#333333",
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 2},
    )
    ax.legend(
        ncol=3,
        fontsize=8,
        frameon=False,
        bbox_to_anchor=(0.5, 1.08),
        loc="lower center",
    )
    add_success_rate_note(ax, success_note, y=0.52)
    plt.tight_layout(rect=[0, 0, 1, 0.84])
    save_current_figure(output_file)
    print(f"✓ Saved: {output_file}")
    plt.close()


if __name__ == "__main__":
    csv_path = "Network-live-migration/metrics/migration_metrics.csv"
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]

    output_path = None
    if len(sys.argv) > 2:
        output_path = sys.argv[2]

    title_suffix = ""
    if len(sys.argv) > 3:
        title_suffix = sys.argv[3]

    plot_phase_breakdown(csv_path, output_path, title_suffix)
