#!/usr/bin/env python3
"""Generate separate downtime and migration-time CDF plots from plotted datasets."""

from __future__ import annotations

import re
import os
import shutil
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.lines import Line2D


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "cdf_plots"

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
    "WiFi 6": "#4E79A7",
    "5G": "#F28E2B",
    "LTE": "#59A14F",
    "Starlink": "#E15759",
    "TEE Best": "#B07AA1",
    "TEE Avg": "#76B7B2",
    "TEE Worst": "#9C755F",
}
COMPARISON_SETS = {
    "all_profiles": {
        "label": "All Profiles",
        "profiles": [PROFILE_LABELS[p] for p in PROFILE_ORDER],
    },
    "access_networks": {
        "label": "Access Networks",
        "profiles": ["WiFi 6", "5G", "LTE", "Starlink"],
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


def apply_transfer_setup_adjustment(df: pd.DataFrame) -> pd.DataFrame:
    """Match the setup-adjusted timing used by the existing plot scripts."""
    adjusted = df.copy()
    if "transfer_ms" not in adjusted.columns:
        return adjusted

    def numeric(column: str) -> pd.Series:
        if column not in adjusted.columns:
            return pd.Series(0.0, index=adjusted.index)
        return pd.to_numeric(adjusted[column], errors="coerce").fillna(0.0)

    setup = numeric("transfer_setup_ms")
    archive_create = numeric("archive_create_ms")
    unpack = numeric("unpack_ms")
    raw_transfer = numeric("transfer_ms")
    adjusted["raw_transfer_ms"] = raw_transfer
    adjusted["transfer_setup_removed_ms"] = setup
    adjusted["transfer_ms"] = (raw_transfer - setup + archive_create + unpack).clip(
        lower=0.0
    )

    if "downtime_ms" in adjusted.columns:
        raw_downtime = numeric("downtime_ms")
        adjusted["raw_downtime_ms"] = raw_downtime
        adjusted["downtime_ms"] = (raw_downtime - setup + archive_create + unpack).clip(
            lower=0.0
        )

    if "total_ms" in adjusted.columns:
        raw_total = numeric("total_ms")
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
        df = apply_transfer_setup_adjustment(df)
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
        key=lambda col: col.astype(str),
    )
    return failures


def failure_legend_handles(failures: pd.DataFrame) -> list[Line2D]:
    handles: list[Line2D] = []
    for _, row in failures.iterrows():
        label = (
            f"{row['profile_label']} / {row['method_label']}: "
            f"{int(row['failed_runs'])} failed"
        )
        handles.append(
            Line2D(
                [0],
                [0],
                color="#555555",
                marker="x",
                linestyle="None",
                markersize=6,
                label=label,
            )
        )
    return handles


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

    sns.set_theme(style="whitegrid", context="paper", font_scale=1.15)
    fig, ax = plt.subplots(figsize=(14.5, 7.0))

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
                linewidth=2.0,
                marker=METHOD_MARKERS[method],
                markevery=max(1, len(x) // 6),
                markersize=5,
                label=f"{profile} / {method}",
            )

    ax.set_xlim(left=0)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_yticklabels([f"{tick}%" for tick in [0, 20, 40, 60, 80, 100]])
    ax.set_xlabel(metric["label"])
    ax.set_ylabel("Cumulative runs (%)")
    ax.set_title(
        f"{metric['title']} - {benchmark} - {mode.title()} - {comparison_label}"
    )
    ax.ticklabel_format(
        axis="x", style="sci", scilimits=(0, 0), useOffset=False, useMathText=False
    )

    count_subset = counts[
        counts["transfer_mode"].eq(mode)
        & counts["benchmark"].eq(benchmark)
        & counts["profile_label"].isin(profile_labels)
    ].copy()
    total_success = int(count_subset["successful_runs"].sum())
    total_attempted = int(count_subset["attempted_runs"].sum())
    failures = failure_rows(count_subset)
    success_handle = Line2D(
        [0],
        [0],
        color="none",
        marker="",
        linestyle="None",
        label=f"Successful: {total_success}/{total_attempted}",
    )

    profile_handles = [
        plt.Line2D([0], [0], color=PROFILE_COLORS[profile], lw=2.5, label=profile)
        for profile in profile_labels
        if subset["profile_label"].eq(profile).any()
    ]
    method_handles = [
        plt.Line2D(
            [0],
            [0],
            color="#333333",
            lw=1.8,
            linestyle=METHOD_LINESTYLES[method],
            marker=METHOD_MARKERS[method],
            markersize=6,
            label=method,
        )
        for method in [METHOD_LABELS[m] for m in METHOD_ORDER]
        if subset["method_label"].eq(method).any()
    ]
    first_legend = ax.legend(
        handles=profile_handles,
        title="Network profile",
        bbox_to_anchor=(1.01, 1.0),
        loc="upper left",
        frameon=True,
        borderaxespad=0,
    )
    ax.add_artist(first_legend)
    second_legend = ax.legend(
        handles=method_handles,
        title="Migration method",
        bbox_to_anchor=(1.01, 0.56),
        loc="upper left",
        frameon=True,
        borderaxespad=0,
    )
    ax.add_artist(second_legend)

    run_status_handles = [success_handle]
    if not failures.empty:
        run_status_handles.extend(failure_legend_handles(failures))
    ax.legend(
        handles=run_status_handles,
        title="Run status",
        bbox_to_anchor=(1.01, 0.33),
        loc="upper left",
        frameon=True,
        borderaxespad=0,
        handlelength=1.4,
        handletextpad=0.6,
    )
    fig.tight_layout(rect=[0, 0, 0.84, 1])

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


def main() -> None:
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
    for comparison_key, comparison in COMPARISON_SETS.items():
        for metric_name in METRICS:
            for mode in MODE_ORDER:
                for benchmark in BENCHMARK_ORDER:
                    out_path = plot_single_ecdf(
                        data=data,
                        counts=counts,
                        metric_name=metric_name,
                        mode=mode,
                        benchmark=benchmark,
                        comparison_key=comparison_key,
                        comparison_label=comparison["label"],
                        profile_labels=comparison["profiles"],
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
