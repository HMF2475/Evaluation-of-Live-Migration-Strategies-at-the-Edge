from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


_TRANSFER_MODE_RE = re.compile(r"(?:^|;)\s*transfer_mode=(host|direct)\b")
_STANDARD_METHOD_ORDER = ["cold", "precopy", "postcopy"]
_NUMERIC_COLUMNS = [
    "checkpoint_ms",
    "archive_bytes",
    "transfer_ms",
    "restore_ms",
    "downtime_ms",
    "bandwidth_mbps",
    "predump_ms",
    "final_dump_ms",
    "total_ms",
    "lazy_pages_active_ms",
    "lazy_pages_log_bytes",
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


def _apply_transfer_setup_adjustment(df: pd.DataFrame) -> None:
    """Use setup-adjusted migration-window timing for plots.

    Raw CRIU rows keep archive creation and destination unpack as separate
    fields, while raw `transfer_ms` only covers the transfer helper. For plots,
    the transfer phase represents the whole window between checkpoint completion
    and restore start, excluding only pre-established setup overhead.
    """
    if "transfer_ms" not in df.columns:
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
    unpack = (
        pd.to_numeric(df["unpack_ms"], errors="coerce").fillna(0.0)
        if "unpack_ms" in df.columns
        else 0.0
    )
    raw_transfer = pd.to_numeric(df["transfer_ms"], errors="coerce").fillna(0.0)
    df["raw_transfer_ms"] = raw_transfer
    df["transfer_setup_removed_ms"] = setup
    df["transfer_ms"] = (raw_transfer - setup + archive_create + unpack).clip(lower=0.0)

    if "downtime_ms" in df.columns:
        raw_downtime = pd.to_numeric(df["downtime_ms"], errors="coerce").fillna(0.0)
        df["raw_downtime_ms"] = raw_downtime
        df["downtime_ms"] = (raw_downtime - setup + archive_create + unpack).clip(
            lower=0.0
        )


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

    _apply_transfer_setup_adjustment(df)
    return df


def ordered_methods(values) -> list[str]:
    seen = {str(v) for v in values if str(v)}
    ordered = [m for m in _STANDARD_METHOD_ORDER if m in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return ordered


def default_plots_dir() -> Path:
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent.parent / "metrics" / "plots"


def resolve_output_file(output_file: Optional[str], default_name: str) -> Path:
    if output_file:
        return Path(output_file)
    return default_plots_dir() / default_name


def apply_plot_theme() -> None:
    sns.set_theme(
        style="whitegrid",
        context="notebook",
        font_scale=1.05,
        rc={"axes.spines.right": False, "axes.spines.top": False},
    )


def phase_colors(labels: list[str]) -> dict[str, tuple[float, float, float]]:
    palette = sns.color_palette("deep", n_colors=max(1, len(labels)))
    return dict(zip(labels, palette))


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
        fontsize=6,
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
