from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.ticker import MaxNLocator


_TRANSFER_MODE_RE = re.compile(r"(?:^|;)\s*transfer_mode=(host|direct)\b")
_LOG_RE = re.compile(r"^(?P<event>.+) - (?P<sec>\d+) sec - (?P<nsec>\d+) nsec$")
_STANDARD_METHOD_ORDER = ["Wasm", "cold", "precopy", "postcopy"]
TRANSFER_MODE_ORDER = ["host", "direct", "unknown"]
TRANSFER_MODE_PALETTE = {
    "host": "#4E79A7",
    "direct": "#F28E2B",
    "unknown": "#8C8C8C",
}
METHOD_PALETTE = {
    "cold": "#4E79A7",
    "precopy": "#F28E2B",
    "postcopy": "#59A14F",
    "Wasm": "#B07AA1",
}
PHASE_PALETTE = {
    "checkpoint": "#4E79A7",
    "transfer (excl. setup)": "#F28E2B",
    "restore": "#59A14F",
    "archive create": "#4E79A7",
    "copy leg 1": "#F28E2B",
    "copy leg 2": "#59A14F",
    "copy": "#F28E2B",
    "cleanup": "#E15759",
    "destination unpack": "#76B7B2",
}
PLOT_FIGSIZE = (9.2, 4.2)
PLOT_LEGEND_FONTSIZE = 12
PLOT_LEGEND_TITLE_FONTSIZE = 13
PLOT_NOTE_FONTSIZE = 12.5
_NUMERIC_COLUMNS = [
    "checkpoint_ms",
    "checkpoint_us",
    "checkpoint_ns",
    "archive_bytes",
    "transfer_ms",
    "restore_ms",
    "downtime_ms",
    "total_ms",
    "predump_ms",
    "final_dump_ms",
    "archive_create_ms",
    "transfer_setup_ms",
    "transfer_send_ms",
    "transfer_receive_ms",
    "transfer_cleanup_ms",
    "unpack_ms",
]


def parse_transfer_mode(notes: str) -> str:
    if not isinstance(notes, str):
        return "unknown"
    m = _TRANSFER_MODE_RE.search(notes)
    return m.group(1) if m else "unknown"


def _find_run_artifacts_dir(csv_file: str) -> Optional[Path]:
    csv_path = Path(csv_file).resolve()
    for parent in [csv_path.parent, *csv_path.parents]:
        candidate = parent / "run_artifacts"
        if candidate.exists():
            return candidate
    return None


def _checkpoint_ns_from_source_log(source_log: Path) -> int:
    events: dict[str, int] = {}
    if not source_log.exists():
        return 0
    for line in source_log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LOG_RE.match(line.strip())
        if not match:
            continue
        events[match.group("event")] = int(match.group("sec")) * 1_000_000_000 + int(
            match.group("nsec")
        )
    start = events.get("request_server - checkpoint start")
    end = events.get("request_server - checkpoint completed")
    if start is None or end is None:
        return 0
    return max(0, end - start)


def _ceil_us(delta_ns: int) -> int:
    if delta_ns == 0:
        return 0
    return max(1, int((delta_ns + 999) // 1_000))


def _backfill_checkpoint_precision(df: pd.DataFrame, csv_file: str) -> None:
    if "run_id" not in df.columns:
        return

    artifacts_dir = _find_run_artifacts_dir(csv_file)
    if artifacts_dir is None:
        return

    if "checkpoint_ns" not in df.columns:
        df["checkpoint_ns"] = 0
    if "checkpoint_us" not in df.columns:
        df["checkpoint_us"] = 0

    cache: dict[str, int] = {}
    for idx, row in df.iterrows():
        if float(row.get("checkpoint_ns", 0) or 0) > 0:
            continue
        run_id = str(row["run_id"])
        if run_id not in cache:
            cache[run_id] = _checkpoint_ns_from_source_log(
                artifacts_dir / run_id / "source.log"
            )
        checkpoint_ns = cache[run_id]
        if checkpoint_ns > 0:
            df.at[idx, "checkpoint_ns"] = checkpoint_ns
            df.at[idx, "checkpoint_us"] = _ceil_us(checkpoint_ns)


def _apply_transfer_setup_adjustment(df: pd.DataFrame) -> None:
    """Use setup-adjusted transfer timing for plots.

    The Wasm benchmark records archive creation separately, but destination
    setup/unpack is already inside `restore_ms` because the restore timer starts
    before destination seeding. Therefore, plots add archive creation to the
    transfer phase and subtract transfer setup, without adding `unpack_ms` again.
    Plotted downtime is computed later from the same phase values used by the
    phase-breakdown figure.
    """
    if "transfer_ms" not in df.columns:
        return
    if "raw_transfer_ms" in df.columns:
        return

    setup = (
        pd.to_numeric(df["transfer_setup_ms"], errors="coerce").fillna(0.0)
        if "transfer_setup_ms" in df.columns
        else 0.0
    )
    archive_create = (
        pd.to_numeric(df["archive_create_ms"], errors="coerce").fillna(0.0)
        if "archive_create_ms" in df.columns
        else 0.0
    )
    raw_transfer = pd.to_numeric(df["transfer_ms"], errors="coerce").fillna(0.0)
    df["raw_transfer_ms"] = raw_transfer
    df["transfer_setup_removed_ms"] = setup
    df["transfer_ms"] = (raw_transfer - setup + archive_create).clip(lower=0.0)

    if "downtime_ms" in df.columns:
        raw_downtime = pd.to_numeric(df["downtime_ms"], errors="coerce").fillna(0.0)
        df["raw_downtime_ms"] = raw_downtime


def load_migration_csv(csv_file: str) -> pd.DataFrame:
    df = pd.read_csv(csv_file)
    if df.empty:
        return df

    for column in _NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)

    if "notes" in df.columns:
        df["transfer_mode"] = df["notes"].apply(parse_transfer_mode)
    else:
        df["transfer_mode"] = "unknown"

    if "migration_method" in df.columns:
        df["migration_method"] = df["migration_method"].replace(
            {"cold": "Wasm", "wasm": "Wasm"}
        )

    _backfill_checkpoint_precision(df, csv_file)
    _apply_transfer_setup_adjustment(df)

    if "checkpoint_us" in df.columns:
        precise_ms = df["checkpoint_us"] / 1000.0
        fallback_ms = df.get("checkpoint_ms", 0)
        df["checkpoint_plot_ms"] = precise_ms.where(precise_ms > 0, fallback_ms)
        df["checkpoint_plot_us"] = df["checkpoint_us"].where(
            df["checkpoint_us"] > 0, fallback_ms * 1000.0
        )
    elif "checkpoint_ms" in df.columns:
        df["checkpoint_plot_ms"] = df["checkpoint_ms"]
        df["checkpoint_plot_us"] = df["checkpoint_ms"] * 1000.0
    else:
        df["checkpoint_plot_ms"] = 0.0
        df["checkpoint_plot_us"] = 0.0

    if {"downtime_ms", "transfer_ms", "restore_ms"}.issubset(df.columns):
        df["downtime_ms"] = (
            pd.to_numeric(df["checkpoint_plot_ms"], errors="coerce").fillna(0.0)
            + pd.to_numeric(df["transfer_ms"], errors="coerce").fillna(0.0)
            + pd.to_numeric(df["restore_ms"], errors="coerce").fillna(0.0)
        )

    if "archive_bytes" in df.columns:
        df["archive_kib"] = df["archive_bytes"] / 1024.0

    return df


def _success_mask(df: pd.DataFrame) -> pd.Series:
    if "success" not in df.columns:
        return pd.Series(True, index=df.index)
    values = df["success"]
    if values.dtype == bool:
        return values.fillna(False)
    normalized = values.astype(str).str.strip().str.lower()
    return normalized.isin({"true", "1", "yes", "ok", "success", "succeeded"})


def successful_runs_only(df: pd.DataFrame) -> pd.DataFrame:
    """Return the rows that represent migrations validated as successful."""
    if df.empty or "success" not in df.columns:
        return df
    return df[_success_mask(df)].copy()


def success_rate_note(df: pd.DataFrame) -> str:
    """Summarize run status using the same wording as the CDF figures."""
    if df.empty or "success" not in df.columns:
        return ""
    if "migration_method" not in df.columns or "transfer_mode" not in df.columns:
        return ""

    work = df.copy()
    work["_success_bool"] = _success_mask(work)
    failures: list[str] = []
    profiles = work.get("profile_name", pd.Series(dtype=str)).astype(str)
    expected_total = 30 if profiles.str.contains("tee_worst", case=False).any() else 40
    attempted_total = 0
    successful_total = int(work["_success_bool"].sum())
    methods = ordered_methods(work["migration_method"].astype(str))
    modes = [
        m
        for m in ["host", "direct", "unknown"]
        if m in set(work["transfer_mode"].astype(str))
    ] or sorted(work["transfer_mode"].astype(str).unique().tolist())

    for method in methods:
        for mode in modes:
            group = work[
                (work["migration_method"].astype(str) == method)
                & (work["transfer_mode"].astype(str) == mode)
            ]
            if group.empty:
                continue
            total = int(len(group))
            ok = int(group["_success_bool"].sum())
            denominator = expected_total if expected_total > total else total
            attempted_total += denominator
            if ok != denominator:
                method_label = {
                    "cold": "Cold",
                    "precopy": "Pre-copy",
                    "postcopy": "Post-copy",
                    "Wasm": "Wasm",
                }.get(method, method)
                failures.append(f"{method_label} {mode.title()} {denominator - ok}")

    lines = [f"Run status: {successful_total}/{attempted_total} successful"]
    if failures:
        lines.append("Failures - " + ", ".join(failures))
    return "\n".join(lines)


def add_success_rate_note(
    ax: plt.Axes,
    note: str,
    *,
    x: float = 0.5,
    y: float = 0.018,
) -> None:
    if not note:
        return
    ax.figure.text(
        x,
        y,
        note,
        ha="center",
        va="bottom",
        fontsize=PLOT_NOTE_FONTSIZE,
        color="#222222",
        linespacing=1.25,
        bbox={
            "boxstyle": "round,pad=0.35",
            "facecolor": "white",
            "edgecolor": "#d9d9d9",
            "alpha": 1.0,
        },
    )


def place_figure_legend(
    fig: plt.Figure,
    handles,
    labels=None,
    *,
    title: str,
    ncol: int,
):
    """Place a CDF-style boxed legend above a compact figure."""
    return fig.legend(
        handles=handles,
        labels=labels,
        title=title,
        bbox_to_anchor=(0.5, 0.985),
        loc="upper center",
        frameon=True,
        borderaxespad=0,
        ncol=max(1, ncol),
        fontsize=PLOT_LEGEND_FONTSIZE,
        title_fontsize=PLOT_LEGEND_TITLE_FONTSIZE,
    )


def ordered_methods(values) -> list[str]:
    seen = {str(v) for v in values if str(v)}
    ordered = [m for m in _STANDARD_METHOD_ORDER if m in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return ordered


def ordered_transfer_modes(values) -> list[str]:
    seen = {str(v) for v in values if str(v)}
    ordered = [m for m in TRANSFER_MODE_ORDER if m in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return ordered


def transfer_mode_palette(values) -> dict[str, str]:
    return {mode: TRANSFER_MODE_PALETTE.get(mode, "#8C8C8C") for mode in values}


def migration_method_palette(values) -> dict[str, str]:
    return {method: METHOD_PALETTE.get(method, "#8C8C8C") for method in values}


def default_plots_dir() -> Path:
    script_dir = Path(__file__).resolve().parent  # WASM-migration/scripts/visualization
    return script_dir.parent.parent / "metrics" / "plots"


def resolve_output_file(output_file: Optional[str], default_name: str) -> Path:
    if output_file:
        return Path(output_file)
    return default_plots_dir() / default_name


def save_current_figure(output_file: Path | str, *, dpi: int = 300) -> None:
    """Save the active matplotlib figure as PNG and a thesis-ready PDF."""
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.savefig(path.with_suffix(".pdf"), bbox_inches="tight")


def apply_plot_theme() -> None:
    sns.set_theme(
        style="whitegrid",
        context="talk",
        font_scale=1.0,
        rc={
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.formatter.useoffset": False,
            "axes.formatter.use_mathtext": False,
            "axes.titlesize": 15,
            "axes.labelsize": 15,
            "xtick.labelsize": 13,
            "ytick.labelsize": 13,
            "legend.fontsize": PLOT_LEGEND_FONTSIZE,
            "legend.title_fontsize": PLOT_LEGEND_TITLE_FONTSIZE,
        },
    )


def phase_colors(labels: list[str]) -> dict[str, tuple[float, float, float]]:
    colors: dict[str, str] = {}
    fallback = sns.color_palette("colorblind", n_colors=max(1, len(labels)))
    fallback_idx = 0
    for label in labels:
        phase = label.split(": ", 1)[-1]
        color = PHASE_PALETTE.get(phase)
        if color is None:
            color = fallback[fallback_idx % len(fallback)]
            fallback_idx += 1
        colors[label] = color
    return colors


def format_plain_axes(ax: plt.Axes, *axes: str) -> None:
    for axis in axes:
        ax.ticklabel_format(
            axis=axis,
            style="plain",
            useOffset=False,
            useMathText=False,
        )
        getattr(ax, f"{axis}axis").set_major_locator(MaxNLocator(nbins=5))


def format_std_label(value: float) -> str:
    if value >= 100:
        return f"+/-{value:.0f}"
    if value >= 10:
        return f"+/-{value:.1f}"
    return f"+/-{value:.2f}"


def annotate_segment_std(
    ax: plt.Axes,
    x: float,
    bottom: float,
    height: float,
    std: float,
    y_upper: float,
) -> None:
    if height <= 0:
        return

    min_inside_height = y_upper * 0.045
    if height >= min_inside_height:
        y = bottom + height / 2
        va = "center"
    else:
        y = bottom + height + y_upper * 0.006
        va = "bottom"

    ax.text(
        x,
        y,
        format_std_label(float(std)),
        ha="center",
        va=va,
        fontsize=8,
        color="#222222",
        bbox={
            "facecolor": "white",
            "edgecolor": "none",
            "alpha": 0.82,
            "pad": 1.2,
        },
        clip_on=True,
        zorder=10,
    )


def annotate_segment_stds(
    ax: plt.Axes,
    records: list[tuple[float, float, float, float]],
    y_upper: float,
) -> None:
    """Draw every finite SD without making thin segments look larger."""
    min_inside_height = y_upper * 0.045
    thin_by_bar: dict[float, list[tuple[float, float, float, float]]] = {}

    for x, bottom, height, std in records:
        if not math.isfinite(std) or std <= 0:
            continue
        if height >= min_inside_height:
            annotate_segment_std(ax, x, bottom, height, std, y_upper)
        else:
            thin_by_bar.setdefault(round(x, 6), []).append((x, bottom, height, std))

    min_gap = y_upper * 0.04
    min_label_y = y_upper * 0.035
    max_label_y = y_upper * 0.965
    for thin_records in thin_by_bar.values():
        thin_records.sort(key=lambda item: item[1] + item[2] / 2)
        targets = [bottom + height / 2 for _, bottom, height, _ in thin_records]
        label_positions: list[float] = []
        for target in targets:
            label_y = max(min_label_y, target)
            if label_positions:
                label_y = max(label_y, label_positions[-1] + min_gap)
            label_positions.append(label_y)

        if label_positions and label_positions[-1] > max_label_y:
            label_positions[-1] = max_label_y
            for idx in range(len(label_positions) - 2, -1, -1):
                label_positions[idx] = min(
                    label_positions[idx], label_positions[idx + 1] - min_gap
                )

        for (x, bottom, height, std), target_y, label_y in zip(
            thin_records, targets, label_positions
        ):
            group_center = round(x)
            direction = -1 if x < group_center else 1
            label_x = x + direction * 0.11
            ax.annotate(
                format_std_label(float(std)),
                xy=(x, target_y),
                xytext=(label_x, label_y),
                ha="right" if direction < 0 else "left",
                va="center",
                fontsize=7.2,
                color="#222222",
                bbox={
                    "facecolor": "white",
                    "edgecolor": "#bdbdbd",
                    "linewidth": 0.4,
                    "alpha": 0.94,
                    "pad": 0.3,
                },
                arrowprops={
                    "arrowstyle": "-",
                    "color": "#777777",
                    "linewidth": 0.55,
                },
                annotation_clip=True,
                zorder=11,
            )
