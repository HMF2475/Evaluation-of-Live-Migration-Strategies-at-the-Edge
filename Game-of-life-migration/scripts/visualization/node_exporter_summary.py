from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from common import (
    add_success_rate_note,
    apply_plot_theme,
    load_migration_csv,
    ordered_methods,
    ordered_transfer_modes,
    PLOT_LEGEND_FONTSIZE,
    PLOT_LEGEND_TITLE_FONTSIZE,
    resolve_output_file,
    resource_method_palette,
    save_current_figure,
    success_rate_note,
    successful_runs_only,
    transfer_mode_palette,
)
from prom_text import iter_samples


_SKIP_DISK_DEV_RE = re.compile(r"^(loop|ram|fd|sr)\d+$")

WANTED: Set[str] = {
    "node_cpu_seconds_total",
    "node_memory_MemTotal_bytes",
    "node_memory_MemAvailable_bytes",
    "node_disk_read_bytes_total",
    "node_disk_written_bytes_total",
    "node_disk_io_time_seconds_total",
}


def _set_compact_method_ticks(ax: plt.Axes, method_order: list[str]) -> None:
    ticks = list(range(len(method_order)))
    label_map = {
        "cold": "cold",
        "precopy": "pre",
        "postcopy": "post",
        "Wasm": "Wasm",
    }
    labels = [
        label_map.get(
            method, method.replace("precopy", "pre").replace("postcopy", "post")
        )
        for method in method_order
    ]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    ax.tick_params(axis="x", labelbottom=True)


def _place_transfer_mode_legend(grid, mode_order: list[str]) -> None:
    if grid._legend is not None:
        grid._legend.remove()
    palette = transfer_mode_palette(mode_order)
    handles = [
        Patch(facecolor=palette[mode], edgecolor="#333333", label=mode.title())
        for mode in mode_order
    ]
    grid.fig.legend(
        handles=handles,
        title="Transfer mode",
        loc="upper center",
        bbox_to_anchor=(0.5, 0.99),
        ncol=max(1, len(mode_order)),
        frameon=True,
        fontsize=PLOT_LEGEND_FONTSIZE,
        title_fontsize=PLOT_LEGEND_TITLE_FONTSIZE,
    )


def _selected_migration_rows(
    csv_file: str,
    *,
    run_id_prefix: Optional[str] = None,
    run_ids: Optional[Set[str]] = None,
) -> pd.DataFrame:
    df = load_migration_csv(csv_file)
    if df.empty:
        return df
    if run_id_prefix:
        df = df[df["run_id"].astype(str).str.startswith(run_id_prefix)].copy()
    if run_ids:
        df = df[df["run_id"].astype(str).isin(run_ids)].copy()
    return df


@dataclass(frozen=True)
class Snapshot:
    cpu: Dict[Tuple[str, str], float]  # (cpu, mode) -> seconds
    mem_total: Optional[float]
    mem_avail: Optional[float]
    disk_read: Dict[str, float]  # device -> bytes
    disk_write: Dict[str, float]
    disk_io_time: Dict[str, float]  # device -> seconds


def load_snapshot(path: Path) -> Snapshot:
    cpu: Dict[Tuple[str, str], float] = {}
    mem_total = None
    mem_avail = None
    disk_read: Dict[str, float] = {}
    disk_write: Dict[str, float] = {}
    disk_io_time: Dict[str, float] = {}

    text = path.read_text(encoding="utf-8", errors="ignore")
    for s in iter_samples(text, wanted=WANTED):
        if s.name == "node_cpu_seconds_total":
            cpu_label = s.labels.get("cpu")
            mode = s.labels.get("mode")
            if cpu_label is None or mode is None:
                continue
            cpu[(cpu_label, mode)] = s.value
        elif s.name == "node_memory_MemTotal_bytes":
            mem_total = s.value
        elif s.name == "node_memory_MemAvailable_bytes":
            mem_avail = s.value
        elif s.name == "node_disk_read_bytes_total":
            dev = s.labels.get("device")
            if not dev or _SKIP_DISK_DEV_RE.match(dev):
                continue
            disk_read[dev] = s.value
        elif s.name == "node_disk_written_bytes_total":
            dev = s.labels.get("device")
            if not dev or _SKIP_DISK_DEV_RE.match(dev):
                continue
            disk_write[dev] = s.value
        elif s.name == "node_disk_io_time_seconds_total":
            dev = s.labels.get("device")
            if not dev or _SKIP_DISK_DEV_RE.match(dev):
                continue
            disk_io_time[dev] = s.value

    return Snapshot(
        cpu=cpu,
        mem_total=mem_total,
        mem_avail=mem_avail,
        disk_read=disk_read,
        disk_write=disk_write,
        disk_io_time=disk_io_time,
    )


def _load_host_time(meta_path: Path) -> Optional[float]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return float(meta.get("captured_at_host_epoch"))
    except Exception:
        return None


def _delta_sum(before: Dict, after: Dict) -> float:
    total = 0.0
    for k, v_after in after.items():
        v_before = before.get(k)
        if v_before is None:
            continue
        total += v_after - v_before
    return total


def compute_cpu_util_pct(before: Snapshot, after: Snapshot) -> Optional[float]:
    if not before.cpu or not after.cpu:
        return None
    total = _delta_sum(before.cpu, after.cpu)
    if total <= 0:
        return None
    idle = 0.0
    iowait = 0.0
    for (cpu_label, mode), v_after in after.cpu.items():
        v_before = before.cpu.get((cpu_label, mode))
        if v_before is None:
            continue
        d = v_after - v_before
        if mode == "idle":
            idle += d
        elif mode == "iowait":
            iowait += d
    busy = total - idle - iowait
    return max(0.0, min(100.0, (busy / total) * 100.0))


def compute_mem_used_pct(snapshot: Snapshot) -> Optional[float]:
    if (
        snapshot.mem_total is None
        or snapshot.mem_avail is None
        or snapshot.mem_total <= 0
    ):
        return None
    return (1.0 - (snapshot.mem_avail / snapshot.mem_total)) * 100.0


def compute_disk_mb_s(
    before: Snapshot, after: Snapshot, dt_s: Optional[float]
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if dt_s is None or dt_s <= 0:
        return None, None, None
    read_b = _delta_sum(before.disk_read, after.disk_read)
    write_b = _delta_sum(before.disk_write, after.disk_write)
    read_mb_s = read_b / dt_s / (1024 * 1024)
    write_mb_s = write_b / dt_s / (1024 * 1024)
    return read_mb_s, write_mb_s, read_mb_s + write_mb_s


def build_node_exporter_dataframe(
    csv_file: str,
    node_metrics_dir: str,
    *,
    run_id_prefix: Optional[str] = None,
    run_ids: Optional[Set[str]] = None,
    source_node: str = "edge-node-1",
    dest_node: str = "edge-node-2",
) -> pd.DataFrame:
    df = _selected_migration_rows(
        csv_file, run_id_prefix=run_id_prefix, run_ids=run_ids
    )
    if df.empty:
        return df
    df = successful_runs_only(df)
    selected_successful_ids = set(df["run_id"].astype(str))

    rows = []
    base = Path(node_metrics_dir)
    for _, r in df.iterrows():
        run_id = str(r["run_id"])
        run_dir = base / run_id
        if not run_dir.exists():
            continue

        def load_pair(
            node: str,
        ) -> Tuple[Optional[Snapshot], Optional[Snapshot], Optional[float]]:
            before_p = run_dir / f"{node}-before.prom"
            after_p = run_dir / f"{node}-after.prom"
            before_m = run_dir / f"{node}-before.json"
            after_m = run_dir / f"{node}-after.json"
            if not before_p.exists() or not after_p.exists():
                return None, None, None
            before_s = load_snapshot(before_p)
            after_s = load_snapshot(after_p)
            t0 = (
                _load_host_time(before_m)
                if before_m.exists()
                else before_p.stat().st_mtime
            )
            t1 = (
                _load_host_time(after_m)
                if after_m.exists()
                else after_p.stat().st_mtime
            )
            dt = float(t1 - t0) if (t0 is not None and t1 is not None) else None
            return before_s, after_s, dt

        src_b, src_a, src_dt = load_pair(source_node)
        dst_b, dst_a, dst_dt = load_pair(dest_node)

        # Process Source Node
        if src_b and src_a:
            src_cpu = compute_cpu_util_pct(src_b, src_a)
            src_mem_after = compute_mem_used_pct(src_a)
            _, _, src_t = compute_disk_mb_s(src_b, src_a, src_dt)

            rows.append(
                {
                    "run_id": run_id,
                    "migration_method": r.get("migration_method"),
                    "transfer_mode": r.get("transfer_mode"),
                    "node": source_node,
                    "node_role": "Source",
                    "cpu_util_pct": src_cpu,
                    "mem_used_pct_after": src_mem_after,
                    "disk_mb_s": src_t,
                }
            )

        # Process Dest Node
        if dst_b and dst_a:
            dst_cpu = compute_cpu_util_pct(dst_b, dst_a)
            dst_mem_after = compute_mem_used_pct(dst_a)
            _, _, dst_t = compute_disk_mb_s(dst_b, dst_a, dst_dt)

            rows.append(
                {
                    "run_id": run_id,
                    "migration_method": r.get("migration_method"),
                    "transfer_mode": r.get("transfer_mode"),
                    "node": dest_node,
                    "node_role": "Destination",
                    "cpu_util_pct": dst_cpu,
                    "mem_used_pct_after": dst_mem_after,
                    "disk_mb_s": dst_t,
                }
            )

    result = pd.DataFrame(rows)
    if not result.empty:
        return result

    aggregate_path = Path(node_metrics_dir).with_name("node_exporter_metrics.csv")
    if not aggregate_path.exists() or not selected_successful_ids:
        return result

    metrics = pd.read_csv(aggregate_path)
    if metrics.empty or "run_id" not in metrics.columns:
        return result
    metrics = metrics[metrics["run_id"].astype(str).isin(selected_successful_ids)]
    if metrics.empty:
        return result

    rows = []
    for _, r in metrics.iterrows():
        common = {
            "run_id": str(r["run_id"]),
            "migration_method": r.get("migration_method"),
            "transfer_mode": r.get("transfer_mode"),
        }
        rows.append(
            {
                **common,
                "node": r.get("source_node", source_node),
                "node_role": "Source",
                "cpu_util_pct": r.get("src_cpu_util_pct"),
                "mem_used_pct_after": r.get("src_mem_used_pct_after"),
                "disk_mb_s": r.get("src_disk_total_mb_s"),
            }
        )
        rows.append(
            {
                **common,
                "node": r.get("dest_node", dest_node),
                "node_role": "Destination",
                "cpu_util_pct": r.get("dst_cpu_util_pct"),
                "mem_used_pct_after": r.get("dst_mem_used_pct_after"),
                "disk_mb_s": r.get("dst_disk_total_mb_s"),
            }
        )

    return pd.DataFrame(rows)


def plot_node_exporter_summary(
    csv_file: str,
    node_metrics_dir: str,
    output_file: str = None,
    *,
    run_id_prefix: Optional[str] = None,
    run_ids: Optional[Set[str]] = None,
    transfer_mode: Optional[str] = None,
    show_run_status: bool = True,
) -> None:
    out = resolve_output_file(output_file, "node_exporter_summary.png")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    selected_rows = _selected_migration_rows(
        csv_file, run_id_prefix=run_id_prefix, run_ids=run_ids
    )
    if transfer_mode:
        selected_rows = selected_rows[
            selected_rows["transfer_mode"].astype(str).eq(transfer_mode)
        ].copy()
    run_status = success_rate_note(selected_rows)

    df = build_node_exporter_dataframe(
        csv_file, node_metrics_dir, run_id_prefix=run_id_prefix, run_ids=run_ids
    )
    if transfer_mode and not df.empty:
        df = df[df["transfer_mode"].astype(str).eq(transfer_mode)].copy()
    if df.empty:
        print(
            "! No node_exporter snapshots found for selected runs (skipping node_exporter plots)."
        )
        return

    df_avg = (
        df.groupby(["run_id", "migration_method", "transfer_mode"], as_index=False)
        .agg(
            {"cpu_util_pct": "mean", "disk_mb_s": "mean", "mem_used_pct_after": "mean"}
        )
        .rename(
            columns={
                "cpu_util_pct": "cpu_util_pct_avg",
                "disk_mb_s": "disk_mb_s_avg",
                "mem_used_pct_after": "mem_used_pct_after_avg",
            }
        )
    )

    melted_avg = df_avg.melt(
        id_vars=["run_id", "migration_method", "transfer_mode"],
        value_vars=["cpu_util_pct_avg", "disk_mb_s_avg", "mem_used_pct_after_avg"],
        var_name="metric",
        value_name="value",
    )
    metric_labels_avg = {
        "cpu_util_pct_avg": "CPU avg (%)",
        "disk_mb_s_avg": "Disk IO avg (MB/s)",
        "mem_used_pct_after_avg": "Mem avg (%)",
    }
    melted_avg["metric"] = (
        melted_avg["metric"].map(metric_labels_avg).fillna(melted_avg["metric"])
    )

    apply_plot_theme()
    method_order = ordered_methods(melted_avg["migration_method"].astype(str))
    mode_order = ordered_transfer_modes(melted_avg["transfer_mode"].astype(str))
    mode_palette = transfer_mode_palette(mode_order)
    hue_column = "migration_method" if transfer_mode else "transfer_mode"
    hue_order = method_order if transfer_mode else mode_order
    hue_palette = (
        resource_method_palette(method_order) if transfer_mode else mode_palette
    )
    g1 = sns.catplot(
        data=melted_avg,
        x="migration_method",
        y="value",
        hue=hue_column,
        hue_order=hue_order,
        palette=hue_palette,
        order=method_order,
        col="metric",
        kind="box",
        sharey=False,
        showfliers=False,
        dodge=not bool(transfer_mode),
        height=2.8,
        aspect=1.18,
        legend=not bool(transfer_mode),
        legend_out=False,
    )
    g1.set_titles("{col_name}")
    g1.set_xlabels("")
    g1.set_ylabels("")
    for ax in g1.axes.flat:
        ax.ticklabel_format(axis="y", style="plain", useOffset=False, useMathText=False)
        ax.set_title(ax.get_title(), fontsize=15, pad=9)
        _set_compact_method_ticks(ax, method_order)
    if transfer_mode:
        g1.fig.suptitle(
            f"{transfer_mode.title()} transfer mode",
            fontsize=16,
            y=0.99,
        )
    else:
        _place_transfer_mode_legend(g1, mode_order)
    if show_run_status:
        add_success_rate_note(g1.axes.flat[0], run_status)
        bottom = 0.23 + 0.05 * run_status.count("\n")
    else:
        bottom = 0.12
    top = 0.80 if transfer_mode else 0.66
    g1.fig.subplots_adjust(top=top, bottom=bottom, wspace=0.28)
    save_current_figure(out)
    print(f"✓ Saved: {out}")
    plt.close()

    out_node = out.parent / "node_exporter_by_node.png"

    melted_node = df.melt(
        id_vars=["run_id", "migration_method", "transfer_mode", "node", "node_role"],
        value_vars=["cpu_util_pct", "disk_mb_s", "mem_used_pct_after"],
        var_name="metric",
        value_name="value",
    )

    metric_labels_node = {
        "cpu_util_pct": "CPU (%)",
        "disk_mb_s": "Disk IO (MB/s)",
        "mem_used_pct_after": "Mem used (%)",
    }
    melted_node["metric"] = (
        melted_node["metric"].map(metric_labels_node).fillna(melted_node["metric"])
    )

    apply_plot_theme()
    melted_node["node"] = melted_node["node"].replace(
        {
            "edge-node-1": "Source",
            "edge-node-2": "Destination",
        }
    )
    g2 = sns.catplot(
        data=melted_node,
        x="migration_method",
        y="value",
        hue=hue_column,
        hue_order=hue_order,
        palette=hue_palette,
        order=method_order,
        col="metric",
        row="node",
        kind="box",
        sharey=False,
        showfliers=False,
        dodge=not bool(transfer_mode),
        height=2.65,
        aspect=1.25,
        legend=not bool(transfer_mode),
        legend_out=False,
    )

    g2.set_titles("{row_name} | {col_name}")
    g2.set_xlabels("")
    g2.set_ylabels("")
    for ax in g2.axes.flat:
        ax.ticklabel_format(axis="y", style="plain", useOffset=False, useMathText=False)
        ax.set_title(ax.get_title(), fontsize=15, pad=9)
        _set_compact_method_ticks(ax, method_order)
    for ax in g2.axes[0]:
        ax.tick_params(axis="x", labelbottom=False)

    if transfer_mode:
        g2.fig.suptitle(
            f"{transfer_mode.title()} transfer mode",
            fontsize=16,
            y=0.995,
        )
    else:
        _place_transfer_mode_legend(g2, mode_order)
    if show_run_status:
        add_success_rate_note(g2.axes.flat[0], run_status)
        bottom = 0.23 + 0.05 * run_status.count("\n")
    else:
        bottom = 0.10
    top = 0.86 if transfer_mode else 0.70
    g2.fig.subplots_adjust(top=top, bottom=bottom, hspace=0.58, wspace=0.28)
    save_current_figure(out_node)
    print(f"✓ Saved: {out_node}")
    plt.close()
