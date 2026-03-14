#!/usr/bin/env python3
"""
plot_comparison.py — Visualisation of container vs WASM migration benchmarks.

Generates publication-quality plots comparing migration strategies across:
- Service downtime
- Total migration time
- Data transferred
- CPU and memory utilisation (if system metrics are available)

Usage:
    python3 plot_comparison.py --results-dir ../results --output-dir plots/
    python3 plot_comparison.py --results-dir ../results --format pdf
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from typing import Optional

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend for headless environments
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
except ImportError:
    print("ERROR: matplotlib and numpy required. Run: pip install matplotlib numpy", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Colour palette — accessible, print-friendly
# --------------------------------------------------------------------------- #
COLORS = {
    "cold":      "#2196F3",   # blue
    "pre_copy":  "#4CAF50",   # green
    "post_copy": "#FF9800",   # orange
    "hybrid":    "#9C27B0",   # purple
    "wasm":      "#F44336",   # red
}
STRATEGY_LABELS = {
    "cold":      "Cold",
    "pre_copy":  "Pre-copy",
    "post_copy": "Post-copy",
    "hybrid":    "Hybrid",
    "wasm":      "WASM",
}
STRATEGY_ORDER = ["cold", "pre_copy", "post_copy", "hybrid", "wasm"]


def load_results(results_dir: str) -> list[dict]:
    all_results: list[dict] = []
    for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                all_results.extend(data)
            elif isinstance(data, dict):
                # Skip system-monitor / metrics files
                if "migration_type" in data or "strategy" in data:
                    all_results.append(data)
        except (json.JSONDecodeError, OSError):
            pass
    return all_results


def group_by_strategy(results: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        s = r.get("migration_type") or r.get("strategy") or "unknown"
        groups[s].append(r)
    return dict(groups)


def extract_values(results: list[dict], nested_key: str) -> list[float]:
    """Extract a (possibly nested) numeric value from a list of result dicts."""
    vals = []
    for r in results:
        obj = r
        for part in nested_key.split("."):
            if not isinstance(obj, dict):
                obj = None
                break
            obj = obj.get(part)
        if obj is not None and not isinstance(obj, dict):
            try:
                vals.append(float(obj))
            except (TypeError, ValueError):
                pass
    return vals


def make_bar_chart(
    groups: dict[str, list[dict]],
    metric_key: str,
    title: str,
    ylabel: str,
    output_path: str,
    fmt: str = "png",
) -> None:
    strategies = [s for s in STRATEGY_ORDER if s in groups]
    means, errors, labels, colors = [], [], [], []

    for s in strategies:
        vals = extract_values(groups[s], metric_key)
        if not vals:
            continue
        m = float(np.mean(vals))
        se = float(np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else 0.0
        means.append(m)
        errors.append(se * 1.96)   # 95% CI half-width
        labels.append(STRATEGY_LABELS.get(s, s))
        colors.append(COLORS.get(s, "#607D8B"))

    if not means:
        print(f"[plot] No data for metric '{metric_key}' — skipping {output_path}")
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(means))
    bars = ax.bar(x, means, yerr=errors, capsize=5, color=colors, alpha=0.85,
                  width=0.55, edgecolor="white", linewidth=0.8)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, alpha=0.3, linestyle="--")
    ax.set_axisbelow(True)

    # Annotate bar tops with values
    for bar, m, e in zip(bars, means, errors):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + e + max(means) * 0.01,
                f"{m:.1f}", ha="center", va="bottom", fontsize=10, fontweight="bold")

    plt.tight_layout()
    full_path = f"{output_path}.{fmt}"
    plt.savefig(full_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[plot] Saved {full_path}")


def make_box_plot(
    groups: dict[str, list[dict]],
    metric_key: str,
    title: str,
    ylabel: str,
    output_path: str,
    fmt: str = "png",
) -> None:
    strategies = [s for s in STRATEGY_ORDER if s in groups]
    data, labels, colors = [], [], []

    for s in strategies:
        vals = extract_values(groups[s], metric_key)
        if not vals:
            continue
        data.append(vals)
        labels.append(STRATEGY_LABELS.get(s, s))
        colors.append(COLORS.get(s, "#607D8B"))

    if not data:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(data, patch_artist=True, notch=False, vert=True,
                    medianprops={"color": "white", "linewidth": 2})
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.8)

    ax.set_xticklabels(labels, fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, alpha=0.3, linestyle="--")
    ax.set_axisbelow(True)

    plt.tight_layout()
    full_path = f"{output_path}.{fmt}"
    plt.savefig(full_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[plot] Saved {full_path}")


def make_scatter_tradeoff(
    groups: dict[str, list[dict]],
    output_path: str,
    fmt: str = "png",
) -> None:
    """Scatter plot: downtime (x) vs data transferred (y) — trade-off view."""
    fig, ax = plt.subplots(figsize=(8, 6))

    for strategy in STRATEGY_ORDER:
        if strategy not in groups:
            continue
        downtime = extract_values(groups[strategy], "timings_ms.total_downtime")
        data_mb = extract_values(groups[strategy], "data_transferred_mb")
        if not downtime or not data_mb:
            continue
        n = min(len(downtime), len(data_mb))
        ax.scatter(downtime[:n], data_mb[:n],
                   color=COLORS.get(strategy, "#607D8B"),
                   label=STRATEGY_LABELS.get(strategy, strategy),
                   s=80, alpha=0.8, zorder=3)

    ax.set_xlabel("Service Downtime (ms)", fontsize=12)
    ax.set_ylabel("Data Transferred (MB)", fontsize=12)
    ax.set_title("Migration Trade-off: Downtime vs Data Transferred",
                 fontsize=14, fontweight="bold", pad=12)
    ax.legend(fontsize=11, framealpha=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(True, alpha=0.3, linestyle="--")

    plt.tight_layout()
    full_path = f"{output_path}.{fmt}"
    plt.savefig(full_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[plot] Saved {full_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot migration benchmark comparison charts")
    parser.add_argument("--results-dir", default="../results", help="Directory containing result JSON files")
    parser.add_argument("--output-dir", default="plots", help="Directory for output plots")
    parser.add_argument("--format", choices=["png", "pdf", "svg"], default="png",
                        help="Output image format (default: png)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    results = load_results(args.results_dir)
    if not results:
        print(f"[plot] No results found in '{args.results_dir}'. Run experiments first.",
              file=sys.stderr)
        sys.exit(1)

    groups = group_by_strategy(results)
    print(f"[plot] Loaded {len(results)} results across {len(groups)} strategies")

    fmt = args.format
    out = args.output_dir

    make_bar_chart(groups, "timings_ms.total_downtime",
                   "Service Downtime by Migration Strategy", "Downtime (ms)",
                   os.path.join(out, "downtime_bar"), fmt)

    make_bar_chart(groups, "timings_ms.total_migration",
                   "Total Migration Time by Strategy", "Migration Time (ms)",
                   os.path.join(out, "migration_time_bar"), fmt)

    make_bar_chart(groups, "data_transferred_mb",
                   "Data Transferred During Migration", "Data (MB)",
                   os.path.join(out, "data_transferred_bar"), fmt)

    make_box_plot(groups, "timings_ms.total_downtime",
                  "Downtime Distribution (Box Plot)", "Downtime (ms)",
                  os.path.join(out, "downtime_box"), fmt)

    make_box_plot(groups, "timings_ms.total_migration",
                  "Migration Time Distribution (Box Plot)", "Migration Time (ms)",
                  os.path.join(out, "migration_time_box"), fmt)

    make_scatter_tradeoff(groups, os.path.join(out, "tradeoff_scatter"), fmt)

    print(f"[plot] All plots saved to '{args.output_dir}/'")


if __name__ == "__main__":
    main()
