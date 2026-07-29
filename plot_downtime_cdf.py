#!/usr/bin/env python3
"""Generate separate downtime and migration-time CDF plots from plotted datasets."""

from __future__ import annotations

import argparse
import re
import os
import shutil
import textwrap
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.ticker import MaxNLocator


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "cdf_plots"
CDF_LEGEND_FONTSIZE = 14
CDF_LEGEND_TITLE_FONTSIZE = 15
CDF_STATUS_FONTSIZE = 14

PROFILE_ORDER = [
    "1_WiFi_6",
    "2_5G",
    "3_LTE",
    "4_Starlink",
    "5_TEE_Best",
    "6_TEE_Avg",
    "7_TEE_Worst",
]
PROFILE_LABELS = {
    "1_WiFi_6": "WiFi 6",
    "2_5G": "5G",
    "3_LTE": "LTE",
    "4_Starlink": "Starlink",
    "5_TEE_Best": "TEE Best",
    "6_TEE_Avg": "TEE Avg",
    "7_TEE_Worst": "TEE Worst",
}
PROFILE_COLORS = {
    # ColorBrewer YlGnBu (9-class), excluding its two palest swatches.
    # The ordered progression remains legible for color-vision deficiencies
    # and when printed, while avoiding a near-white curve.
    "WiFi 6": "#C7E9B4",
    "5G": "#7FCDBB",
    "LTE": "#41B6C4",
    "Starlink": "#1D91C0",
    "TEE Best": "#225EA8",
    "TEE Avg": "#253494",
    "TEE Worst": "#081D58",
}
COMPARISON_SETS = {
    "all_profiles": {
        "label": "All Profiles",
        "profiles": [PROFILE_LABELS[p] for p in PROFILE_ORDER],
    },
    "access_networks": {
        "label": "Access Networks",
        "profiles": ["WiFi 6", "5G", "LTE"],
    },
    "tactical_edge": {
        "label": "Tactical Edge",
        "profiles": ["Starlink", "TEE Best", "TEE Avg", "TEE Worst"],
    },
}

BENCHMARK_LABELS = {
    "Container": "Counter (CRIU)",
    "Network-live-migration": "TCP Client (CRIU)",
    "Game-of-life-migration": "Game of Life (CRIU)",
    "WASM-migration": "WebAssembly",
}
BENCHMARK_ORDER = [
    "Counter (CRIU)",
    "TCP Client (CRIU)",
    "Game of Life (CRIU)",
    "WebAssembly",
]

METHOD_ORDER = ["cold", "precopy", "postcopy", "Wasm"]
METHOD_LABELS = {
    "cold": "Cold",
    "precopy": "Pre-copy",
    "postcopy": "Post-copy",
    "Wasm": "Wasm",
}
METHOD_MARKERS = {
    "Cold": "o",
    "Pre-copy": "^",
    "Post-copy": "s",
    "Wasm": "D",
}
METHOD_LINESTYLES = {
    "Cold": "-",
    "Pre-copy": "--",
    "Post-copy": ":",
    "Wasm": "-.",
}

MODE_ORDER = ["host", "direct"]
RUNS_PER_GROUP = {
    "default": 40,
    "TEE Worst": 30,
}
TRANSFER_MODE_NOTE_RE = re.compile(r"(?:^|;)\s*transfer_mode=(host|direct)\b")
METRICS = {
    "downtime": {
        "column": "downtime_ms",
        "label": "Downtime (ms)",
        "title": "Downtime CDF",
    },
    "migration_time": {
        "column": "migration_time_ms",
        "label": "Migration time (ms)",
        "title": "Migration Time CDF",
    },
}


def parse_transfer_mode(run_id: str) -> str | None:
    match = re.search(r"\d{2}-\d{2}-\d{4}-(host|direct)-", str(run_id))
    return match.group(1) if match else None


def parse_transfer_mode_from_row(row: pd.Series) -> str | None:
    notes = str(row.get("notes", ""))
    match = TRANSFER_MODE_NOTE_RE.search(notes)
    if match:
        return match.group(1)
    return parse_transfer_mode(str(row.get("run_id", "")))


def apply_transfer_setup_adjustment(df: pd.DataFrame, module: str = "") -> pd.DataFrame:
    """Adjust transfer and downtime timing once for plotting."""
    adjusted = df.copy()
    if "transfer_ms" not in adjusted.columns:
        return adjusted

    def numeric(column: str) -> pd.Series:
        if column not in adjusted.columns:
            return pd.Series(0.0, index=adjusted.index)
        return pd.to_numeric(adjusted[column], errors="coerce").fillna(0.0)

    setup = numeric("transfer_setup_ms")
    archive_create = numeric("archive_create_ms")
    unpack = pd.Series(0.0, index=adjusted.index)
    if module != "WASM-migration":
        unpack = numeric("unpack_ms")
    raw_transfer = (
        numeric("raw_transfer_ms")
        if "raw_transfer_ms" in adjusted.columns
        else numeric("transfer_ms")
    )
    adjusted["raw_transfer_ms"] = raw_transfer
    adjusted["transfer_setup_removed_ms"] = setup
    adjusted["transfer_ms"] = (raw_transfer - setup + archive_create + unpack).clip(
        lower=0.0
    )

    if "downtime_ms" in adjusted.columns:
        raw_downtime = numeric("downtime_ms")
        if "raw_downtime_ms" in adjusted.columns:
            raw_downtime = numeric("raw_downtime_ms")
        adjusted["raw_downtime_ms"] = raw_downtime
        checkpoint = numeric("checkpoint_plot_ms")
        if (checkpoint == 0).all():
            checkpoint = numeric("final_dump_ms")
            checkpoint = checkpoint.where(checkpoint > 0, numeric("checkpoint_ms"))
        restore = numeric("restore_ms")
        adjusted["downtime_ms"] = checkpoint + adjusted["transfer_ms"] + restore

    if "total_ms" in adjusted.columns:
        raw_total = numeric("total_ms")
        if "raw_total_ms" in adjusted.columns:
            raw_total = numeric("raw_total_ms")
        adjusted["raw_total_ms"] = raw_total
        adjusted["migration_time_ms"] = (raw_total - setup).clip(lower=0.0)
    elif "downtime_ms" in adjusted.columns:
        adjusted["migration_time_ms"] = adjusted["downtime_ms"]

    return adjusted


def success_mask(df: pd.DataFrame) -> pd.Series:
    if "success" not in df.columns:
        return pd.Series(True, index=df.index)
    values = df["success"]
    if values.dtype == bool:
        return values.fillna(False)
    return (
        values.astype(str)
        .str.strip()
        .str.lower()
        .isin(["true", "1", "yes", "ok", "success", "succeeded"])
    )


def load_latest_plotted_metrics() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    selected: dict[tuple[str, str], Path] = {}

    for csv_path in ROOT.glob("*/metrics/plots/*/filtered_migration_metrics.csv"):
        module = csv_path.relative_to(ROOT).parts[0]
        if module not in BENCHMARK_LABELS:
            continue

        df_head = pd.read_csv(csv_path, nrows=1)
        if "profile_name" not in df_head.columns or df_head.empty:
            continue
        profile = str(df_head.loc[0, "profile_name"])
        if profile not in PROFILE_ORDER:
            continue

        key = (module, profile)
        current = selected.get(key)
        if current is None or csv_path.parent.name > current.parent.name:
            selected[key] = csv_path

    frames: list[pd.DataFrame] = []
    count_frames: list[pd.DataFrame] = []
    failed_detail_frames: list[pd.DataFrame] = []
    for (module, profile), csv_path in sorted(selected.items()):
        df = pd.read_csv(csv_path)
        df = apply_transfer_setup_adjustment(df, module=module)
        if module == "WASM-migration":
            df["migration_method"] = df["migration_method"].replace(
                {"cold": "Wasm", "wasm": "Wasm"}
            )
        df = df[df["migration_method"].isin(METHOD_ORDER)]
        df = df.copy()
        df["benchmark"] = BENCHMARK_LABELS[module]
        df["profile_label"] = df["profile_name"].map(PROFILE_LABELS)
        df["method_label"] = df["migration_method"].map(METHOD_LABELS)
        df["transfer_mode"] = df.apply(parse_transfer_mode_from_row, axis=1)
        df["source_plot_dir"] = str(csv_path.parent.relative_to(ROOT))
        df = df[df["transfer_mode"].isin(MODE_ORDER)]
        df = df[df["profile_label"].notna()]

        group_cols = ["transfer_mode", "profile_label", "benchmark", "method_label"]
        attempts = (
            df.groupby(group_cols, observed=True)
            .size()
            .reset_index(name="attempted_runs")
        )
        successes = (
            df[success_mask(df)]
            .groupby(group_cols, observed=True)
            .size()
            .reset_index(name="successful_runs")
        )
        counts = attempts.merge(successes, on=group_cols, how="left")
        counts["successful_runs"] = counts["successful_runs"].fillna(0).astype(int)
        # A launched run that never produced a metrics row is still part of the
        # planned experiment. Keep the CDF status denominator consistent with
        # the coverage tables: 40 runs per method/mode, or 30 for TEE Worst.
        expected_runs = RUNS_PER_GROUP.get(
            str(PROFILE_LABELS.get(profile, profile)), RUNS_PER_GROUP["default"]
        )
        counts["observed_rows"] = counts["attempted_runs"].astype(int)
        counts["attempted_runs"] = counts["attempted_runs"].clip(lower=expected_runs)
        count_frames.append(counts)

        ok_mask = success_mask(df)
        failed_details = df[~ok_mask].copy()
        if not failed_details.empty:
            keep_cols = [
                "run_id",
                "profile_label",
                "benchmark",
                "method_label",
                "transfer_mode",
                "success",
                "notes",
                "source_plot_dir",
            ]
            keep_cols = [col for col in keep_cols if col in failed_details.columns]
            failed_detail_frames.append(failed_details[keep_cols])

        df = df[ok_mask]
        df = df[df["downtime_ms"].notna()]
        if not df.empty:
            df = df.merge(counts, on=group_cols, how="left")
            frames.append(df)

    if not frames:
        raise SystemExit("No successful filtered_migration_metrics.csv rows found.")

    combined = pd.concat(frames, ignore_index=True)
    counts_combined = pd.concat(count_frames, ignore_index=True)
    failed_details_combined = (
        pd.concat(failed_detail_frames, ignore_index=True)
        if failed_detail_frames
        else pd.DataFrame(
            columns=[
                "run_id",
                "profile_label",
                "benchmark",
                "method_label",
                "transfer_mode",
                "success",
                "notes",
                "source_plot_dir",
            ]
        )
    )
    combined["profile_label"] = pd.Categorical(
        combined["profile_label"],
        categories=[PROFILE_LABELS[p] for p in PROFILE_ORDER],
        ordered=True,
    )
    combined["benchmark"] = pd.Categorical(
        combined["benchmark"],
        categories=BENCHMARK_ORDER,
        ordered=True,
    )
    combined["method_label"] = pd.Categorical(
        combined["method_label"],
        categories=[METHOD_LABELS[m] for m in METHOD_ORDER],
        ordered=True,
    )
    counts_combined["profile_label"] = pd.Categorical(
        counts_combined["profile_label"],
        categories=[PROFILE_LABELS[p] for p in PROFILE_ORDER],
        ordered=True,
    )
    counts_combined["benchmark"] = pd.Categorical(
        counts_combined["benchmark"],
        categories=BENCHMARK_ORDER,
        ordered=True,
    )
    counts_combined["method_label"] = pd.Categorical(
        counts_combined["method_label"],
        categories=[METHOD_LABELS[m] for m in METHOD_ORDER],
        ordered=True,
    )
    counts_combined = counts_combined.sort_values(
        ["profile_label", "benchmark", "transfer_mode", "method_label"]
    )
    failed_details_combined["profile_label"] = pd.Categorical(
        failed_details_combined["profile_label"],
        categories=[PROFILE_LABELS[p] for p in PROFILE_ORDER],
        ordered=True,
    )
    failed_details_combined["benchmark"] = pd.Categorical(
        failed_details_combined["benchmark"],
        categories=BENCHMARK_ORDER,
        ordered=True,
    )
    failed_details_combined["method_label"] = pd.Categorical(
        failed_details_combined["method_label"],
        categories=[METHOD_LABELS[m] for m in METHOD_ORDER],
        ordered=True,
    )
    failed_details_combined = failed_details_combined.sort_values(
        ["profile_label", "benchmark", "transfer_mode", "method_label", "run_id"]
    )
    return combined, counts_combined, failed_details_combined


def safe_name(value: str) -> str:
    return (
        str(value)
        .lower()
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("-", "_")
        .replace("/", "_")
    )


def failure_rows(count_subset: pd.DataFrame) -> pd.DataFrame:
    failures = count_subset.copy()
    failures["failed_runs"] = failures["attempted_runs"].astype(int) - failures[
        "successful_runs"
    ].astype(int)
    failures = failures[failures["failed_runs"] > 0].copy()
    if failures.empty:
        return failures
    failures = failures.sort_values(
        ["profile_label", "method_label"],
    )
    return failures


def _profile_failure_entries(failures: pd.DataFrame) -> list[str]:
    entries: list[str] = []
    method_order = [METHOD_LABELS[method] for method in METHOD_ORDER]
    profile_order = [PROFILE_LABELS[profile] for profile in PROFILE_ORDER]
    for profile in profile_order:
        profile_rows = failures[failures["profile_label"].eq(profile)]
        if profile_rows.empty:
            continue
        methods: list[str] = []
        for method in method_order:
            row = profile_rows[profile_rows["method_label"].eq(method)]
            if row.empty:
                continue
            methods.append(f"{method} {int(row.iloc[0]['failed_runs'])}")
        if methods:
            entries.append(f"{profile}: {', '.join(methods)}")
    return entries


def incomplete_profile_entry(
    benchmark: str,
    comparison_key: str,
    profile_labels: list[str],
) -> str:
    if (
        benchmark == "Game of Life (CRIU)"
        and comparison_key in {"all_profiles", "tactical_edge"}
        and "TEE Worst" in profile_labels
    ):
        return "TEE Worst: no timing samples " "(archive/pre-dump transfer failed)"
    return ""


def run_status_lines(
    failures: pd.DataFrame,
    *,
    successful: int,
    attempted: int,
    incomplete_entry: str = "",
) -> list[str]:
    lines = [f"Run status: {successful}/{attempted} successful"]
    entries = _profile_failure_entries(failures)
    if incomplete_entry:
        entries.append(incomplete_entry)
    if entries:
        lines.extend(
            textwrap.wrap(
                "Failures - " + "; ".join(entries),
                width=100,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return lines


def ecdf_xy(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    x = values.sort_values(ignore_index=True)
    y = pd.Series((range(1, len(x) + 1)), dtype=float) * 100.0 / len(x)
    return x, y


def plot_single_ecdf(
    data: pd.DataFrame,
    counts: pd.DataFrame,
    metric_name: str,
    mode: str,
    benchmark: str,
    comparison_key: str,
    comparison_label: str,
    profile_labels: list[str],
    *,
    x_max_ms: float | None = None,
    show_run_status: bool = True,
) -> Path | None:
    metric = METRICS[metric_name]
    value_column = metric["column"]
    subset = data[
        data["transfer_mode"].eq(mode)
        & data["benchmark"].eq(benchmark)
        & data["profile_label"].isin(profile_labels)
        & data[value_column].notna()
    ].copy()
    if subset.empty:
        return None

    sns.set_theme(
        style="whitegrid",
        context="talk",
        font_scale=1.0,
        rc={
            "axes.labelsize": 17,
            "xtick.labelsize": 15,
            "ytick.labelsize": 15,
        },
    )
    fig, ax = plt.subplots(figsize=(9.6, 4.6))

    for profile in profile_labels:
        for method in [METHOD_LABELS[m] for m in METHOD_ORDER]:
            series = subset[
                subset["profile_label"].eq(profile) & subset["method_label"].eq(method)
            ][value_column]
            if series.empty:
                continue
            x, y = ecdf_xy(series)
            ax.step(
                x,
                y,
                where="post",
                color=PROFILE_COLORS[profile],
                linestyle=METHOD_LINESTYLES[method],
                linewidth=2.5,
                marker=METHOD_MARKERS[method],
                markevery=max(1, len(x) // 6),
                markersize=6,
                label=f"{profile} / {method}",
            )

    subset = subset[pd.to_numeric(subset[value_column], errors="coerce") > 0]
    x_min = float(subset[value_column].min())
    x_max = float(subset[value_column].max())
    if x_min == x_max:
        ax.set_xlim(max(x_min * 0.95, 1.0), x_max * 1.05)
    else:
        visible_x_max = x_max_ms if x_max_ms is not None else x_max
        ax.set_xlim(x_min, visible_x_max)
    ax.ticklabel_format(axis="x", style="plain", useOffset=False)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_yticklabels([f"{tick}%" for tick in [0, 20, 40, 60, 80, 100]])
    ax.set_xlabel(metric["label"])
    ax.set_ylabel("Cumulative runs (%)")
    count_subset = counts[
        counts["transfer_mode"].eq(mode)
        & counts["benchmark"].eq(benchmark)
        & counts["profile_label"].isin(profile_labels)
    ].copy()
    total_success = int(count_subset["successful_runs"].sum())
    total_attempted = int(count_subset["attempted_runs"].sum())
    failures = failure_rows(count_subset)

    profile_handles = [
        plt.Line2D([0], [0], color=PROFILE_COLORS[profile], lw=3.0, label=profile)
        for profile in profile_labels
        if subset["profile_label"].eq(profile).any()
    ]
    method_handles = [
        plt.Line2D(
            [0],
            [0],
            color="#333333",
            lw=2.2,
            linestyle=METHOD_LINESTYLES[method],
            marker=METHOD_MARKERS[method],
            markersize=7,
            label=method,
        )
        for method in [METHOD_LABELS[m] for m in METHOD_ORDER]
        if subset["method_label"].eq(method).any()
    ]
    encoding_handles = profile_handles + method_handles
    encoding_columns = max(len(profile_handles), len(method_handles), 1)
    encoding_legend = fig.legend(
        handles=encoding_handles,
        title="Color: network profile  |  Line and marker: migration method",
        bbox_to_anchor=(0.5, 0.985),
        loc="upper center",
        frameon=True,
        borderaxespad=0,
        ncol=encoding_columns,
        fontsize=CDF_LEGEND_FONTSIZE,
        title_fontsize=CDF_LEGEND_TITLE_FONTSIZE,
    )
    fig.add_artist(encoding_legend)

    if show_run_status:
        status_lines = run_status_lines(
            failures,
            successful=total_success,
            attempted=total_attempted,
            incomplete_entry=incomplete_profile_entry(
                benchmark, comparison_key, profile_labels
            ),
        )
        fig.text(
            0.5,
            0.018,
            "\n".join(status_lines),
            ha="center",
            va="bottom",
            fontsize=CDF_STATUS_FONTSIZE,
            color="#222222",
            linespacing=1.25,
            bbox={
                "boxstyle": "round,pad=0.35",
                "facecolor": "white",
                "edgecolor": "#d0d0d0",
                "alpha": 1.0,
            },
        )
        bottom = 0.27 + 0.055 * max(0, len(status_lines) - 1)
    else:
        bottom = 0.18
    fig.subplots_adjust(left=0.09, right=0.98, bottom=bottom, top=0.69)

    out_dir = OUT_DIR / comparison_key / metric_name / mode
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = (
        out_dir
        / f"{metric_name}_cdf_{comparison_key}_{mode}_{safe_name(benchmark)}.png"
    )
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    fig.savefig(out_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate downtime and migration-time CDF plots."
    )
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        choices=BENCHMARK_ORDER,
        default=None,
        help="Only regenerate the selected workloads.",
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        choices=list(METRICS),
        default=None,
        help="Only regenerate the selected metrics.",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=MODE_ORDER,
        default=None,
        help="Only regenerate the selected transfer modes.",
    )
    parser.add_argument(
        "--comparison-sets",
        nargs="+",
        choices=list(COMPARISON_SETS),
        default=None,
        help="Only regenerate the selected profile comparison sets.",
    )
    parser.add_argument(
        "--x-max-ms",
        type=float,
        default=None,
        help="Cap the visible x-axis at this millisecond value.",
    )
    parser.add_argument(
        "--hide-run-status",
        action="store_true",
        help="Omit the run-status box below each selected plot.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selected_benchmarks = args.benchmarks or BENCHMARK_ORDER
    selected_metrics = args.metrics or list(METRICS)
    selected_modes = args.modes or MODE_ORDER
    selected_comparisons = args.comparison_sets or list(COMPARISON_SETS)

    data, counts, failed_details = load_latest_plotted_metrics()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUT_DIR / "cdf_input.csv", index=False)
    counts.to_csv(OUT_DIR / "cdf_success_counts.csv", index=False)
    failures = failure_rows(counts)
    failures.to_csv(OUT_DIR / "cdf_failed_runs.csv", index=False)
    failed_details.to_csv(OUT_DIR / "cdf_failed_run_ids.csv", index=False)

    # Remove older combined-grid outputs from the previous version of this script.
    for stale in [
        OUT_DIR / "downtime_cdf_host.png",
        OUT_DIR / "downtime_cdf_direct.png",
        OUT_DIR / "downtime_cdf_input.csv",
        OUT_DIR / "downtime_cdf_counts.csv",
    ]:
        if stale.exists():
            stale.unlink()
    full_generation = (
        set(selected_benchmarks) == set(BENCHMARK_ORDER)
        and set(selected_metrics) == set(METRICS)
        and set(selected_modes) == set(MODE_ORDER)
        and set(selected_comparisons) == set(COMPARISON_SETS)
    )
    if full_generation:
        for generated_dir in [
            OUT_DIR / "all_profiles",
            OUT_DIR / "access_networks",
            OUT_DIR / "downtime",
            OUT_DIR / "latency",
            OUT_DIR / "migration_time",
            OUT_DIR / "tactical_edge",
        ]:
            if generated_dir.exists():
                shutil.rmtree(generated_dir)

    written: list[Path] = []
    for comparison_key in selected_comparisons:
        comparison = COMPARISON_SETS[comparison_key]
        for metric_name in selected_metrics:
            for mode in selected_modes:
                for benchmark in selected_benchmarks:
                    out_path = plot_single_ecdf(
                        data=data,
                        counts=counts,
                        metric_name=metric_name,
                        mode=mode,
                        benchmark=benchmark,
                        comparison_key=comparison_key,
                        comparison_label=comparison["label"],
                        profile_labels=comparison["profiles"],
                        x_max_ms=args.x_max_ms,
                        show_run_status=not args.hide_run_status,
                    )
                    if out_path is not None:
                        written.append(out_path)
                        print(f"Saved {out_path.relative_to(ROOT)}")

    summaries = []
    for metric_name, metric in METRICS.items():
        value_column = metric["column"]
        summary = (
            data[data[value_column].notna()]
            .groupby(
                ["transfer_mode", "profile_label", "benchmark", "method_label"],
                observed=True,
            )
            .size()
            .reset_index(name="successful_rows")
        )
        summary.insert(0, "metric", metric_name)
        summaries.append(summary)

    pd.concat(summaries, ignore_index=True).to_csv(
        OUT_DIR / "cdf_counts.csv", index=False
    )
    print(f"Saved {OUT_DIR.relative_to(ROOT) / 'cdf_input.csv'}")
    print(f"Saved {OUT_DIR.relative_to(ROOT) / 'cdf_counts.csv'}")
    print(f"Saved {OUT_DIR.relative_to(ROOT) / 'cdf_success_counts.csv'}")
    print(f"Saved {OUT_DIR.relative_to(ROOT) / 'cdf_failed_runs.csv'}")
    print(f"Saved {OUT_DIR.relative_to(ROOT) / 'cdf_failed_run_ids.csv'}")
    print(f"Generated {len(written)} separated ECDF plots.")

    by_metric = (
        pd.DataFrame({"path": [str(p.relative_to(ROOT)) for p in written]})
        .assign(
            comparison_set=lambda df: df["path"].str.split("/").str[1],
            metric=lambda df: df["path"].str.split("/").str[2],
        )
        .groupby(["comparison_set", "metric"])
        .size()
        .reset_index(name="plots")
    )
    print(by_metric.to_string(index=False))


if __name__ == "__main__":
    main()
