from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
VIS_DIR = REPO_ROOT / "Game-of-life-migration" / "scripts" / "visualization"
if str(VIS_DIR) not in sys.path:
    sys.path.insert(0, str(VIS_DIR))

from common import METHOD_PALETTE, apply_plot_theme  # noqa: E402


CSV_PATH = (
    REPO_ROOT
    / "Game-of-life-migration"
    / "metrics"
    / "plots"
    / "2026-05-15_032437__6_tee_avg__03__game-of-life-migration"
    / "filtered_migration_metrics.csv"
)

OUTPUT_BASE = REPO_ROOT / "presentation_assets" / "gol_tee_avg_direct_downtime_vs_total"
METHODS = ["cold", "precopy"]
METRICS = [
    ("downtime_ms", "Downtime"),
    ("setup_excl_total_ms", "Total migration time\n(excl. setup)"),
]


def load_summary() -> tuple[pd.DataFrame, dict[str, float]]:
    df = pd.read_csv(CSV_PATH)
    df = df[
        (df["transfer_mode"] == "direct")
        & (df["migration_method"].isin(METHODS))
        & (df["success"] == True)
    ].copy()
    df["setup_excl_total_ms"] = pd.to_numeric(df["total_ms"], errors="coerce").fillna(
        0.0
    ) - pd.to_numeric(df["transfer_setup_ms"], errors="coerce").fillna(0.0)

    rows: list[dict[str, float | str]] = []
    for metric_key, metric_label in METRICS:
        for method in METHODS:
            series = pd.to_numeric(
                df.loc[df["migration_method"] == method, metric_key],
                errors="coerce",
            ).dropna()
            rows.append(
                {
                    "metric_key": metric_key,
                    "metric_label": metric_label,
                    "migration_method": method,
                    "count": int(series.size),
                    "mean_ms": float(series.mean()),
                    "median_ms": float(series.median()),
                    "std_ms": float(series.std(ddof=1)),
                }
            )

    summary = pd.DataFrame(rows)
    cold_downtime = float(
        summary.loc[
            (summary["metric_key"] == "downtime_ms")
            & (summary["migration_method"] == "cold"),
            "median_ms",
        ].iloc[0]
    )
    precopy_downtime = float(
        summary.loc[
            (summary["metric_key"] == "downtime_ms")
            & (summary["migration_method"] == "precopy"),
            "median_ms",
        ].iloc[0]
    )
    cold_total = float(
        summary.loc[
            (summary["metric_key"] == "setup_excl_total_ms")
            & (summary["migration_method"] == "cold"),
            "median_ms",
        ].iloc[0]
    )
    precopy_total = float(
        summary.loc[
            (summary["metric_key"] == "setup_excl_total_ms")
            & (summary["migration_method"] == "precopy"),
            "median_ms",
        ].iloc[0]
    )

    ratios = {
        "downtime_factor": cold_downtime / precopy_downtime,
        "downtime_pct": (1.0 - (precopy_downtime / cold_downtime)) * 100.0,
        "total_factor": precopy_total / cold_total,
        "total_pct": ((precopy_total / cold_total) - 1.0) * 100.0,
    }
    return summary, ratios


def add_value_labels(
    ax: plt.Axes,
    bars,
    values_seconds: list[float],
    stds_seconds: list[float],
) -> None:
    ymax = max(v + s for v, s in zip(values_seconds, stds_seconds))
    pad = max(18.0, ymax * 0.025)
    for bar, value_s, std_s in zip(bars, values_seconds, stds_seconds):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + std_s + pad,
            f"{value_s:.1f} s",
            ha="center",
            va="bottom",
            fontsize=10,
        )


def make_plot(summary: pd.DataFrame, ratios: dict[str, float]) -> None:
    apply_plot_theme()
    fig, ax = plt.subplots(figsize=(10.5, 6.0))

    x = np.arange(len(METRICS))
    width = 0.34
    offset = {
        "cold": -width / 2.0,
        "precopy": width / 2.0,
    }

    ymax = 0.0
    bars_by_method = {}
    for method in METHODS:
        subset = summary[summary["migration_method"] == method].set_index("metric_key")
        medians_s = [
            subset.loc[metric_key, "median_ms"] / 1000.0 for metric_key, _ in METRICS
        ]
        stds_s = [
            subset.loc[metric_key, "std_ms"] / 1000.0 for metric_key, _ in METRICS
        ]
        positions = x + offset[method]
        bars = ax.bar(
            positions,
            medians_s,
            width=width,
            color=METHOD_PALETTE[method],
            edgecolor="none",
            label="Cold" if method == "cold" else "Pre-copy",
            yerr=stds_s,
            capsize=6,
            ecolor="#333333",
            error_kw={"elinewidth": 1.4, "capthick": 1.4},
            zorder=3,
        )
        bars_by_method[method] = bars
        add_value_labels(ax, bars, medians_s, stds_s)
        ymax = max(ymax, max(v + e for v, e in zip(medians_s, stds_s)))

    ax.set_xticks(x)
    ax.set_xticklabels([label for _, label in METRICS])
    ax.set_ylabel("Time (s)")
    fig.suptitle("Game of Life, TEE Avg, Direct Mode", y=0.975, fontsize=18)
    fig.text(
        0.125,
        0.905,
        "Bars show median successful runs; whiskers show standard deviation (n=40 each).",
        ha="left",
        va="center",
        fontsize=10,
        color="#444444",
    )
    ax.legend(frameon=True, facecolor="white", edgecolor="#d9d9d9")
    ax.set_axisbelow(True)
    ax.set_ylim(0, ymax * 1.28)

    ax.text(
        x[0],
        ymax * 1.10,
        f"Pre-copy lowers downtime\nby {ratios['downtime_factor']:.2f}x ({ratios['downtime_pct']:.1f}%)",
        ha="center",
        va="bottom",
        fontsize=10,
        bbox={
            "boxstyle": "round,pad=0.35",
            "facecolor": "white",
            "edgecolor": "#d9d9d9",
            "alpha": 0.98,
        },
    )
    ax.text(
        x[1],
        ymax * 1.10,
        f"Pre-copy raises total time\nto {ratios['total_factor']:.2f}x cold ({ratios['total_pct']:.1f}%)",
        ha="center",
        va="bottom",
        fontsize=10,
        bbox={
            "boxstyle": "round,pad=0.35",
            "facecolor": "white",
            "edgecolor": "#d9d9d9",
            "alpha": 0.98,
        },
    )

    fig.tight_layout(rect=(0, 0, 1, 0.86))
    OUTPUT_BASE.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf", ".svg"):
        fig.savefig(OUTPUT_BASE.with_suffix(suffix), dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    summary, ratios = load_summary()
    make_plot(summary, ratios)
    print(f"Saved: {OUTPUT_BASE.with_suffix('.png')}")
    print(f"Saved: {OUTPUT_BASE.with_suffix('.pdf')}")
    print(f"Saved: {OUTPUT_BASE.with_suffix('.svg')}")
    print(
        "Takeaway: pre-copy lowers median downtime "
        f"by {ratios['downtime_factor']:.2f}x but increases median total migration time "
        f"by {ratios['total_factor']:.2f}x."
    )


if __name__ == "__main__":
    main()
