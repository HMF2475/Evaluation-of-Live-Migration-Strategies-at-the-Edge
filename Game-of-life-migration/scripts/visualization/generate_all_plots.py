#!/usr/bin/env python3
"""
Generate a useful set of plots for a benchmark batch.

Supports filtering by run_id prefix (recommended when migration_metrics.csv
accumulates many runs). Also supports filtering by an explicit run-id list file
(recommended when run_ids do not share a common prefix).
"""

from __future__ import annotations

import os
import argparse
from pathlib import Path


_mpl_dir = Path(os.environ.get("MPLCONFIGDIR", "/tmp/matplotlib"))
_mpl_dir.mkdir(parents=True, exist_ok=True)
os.environ["MPLCONFIGDIR"] = str(_mpl_dir)

from plot_downtime import plot_downtime
from plot_transfer_analysis import plot_transfer_analysis
from plot_phase_breakdown import plot_phase_breakdown
from plot_transfer_phase_breakdown import plot_transfer_phase_breakdown
from node_exporter_summary import plot_node_exporter_summary
from common import load_migration_csv, successful_runs_only


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate all plots for migration metrics."
    )
    parser.add_argument(
        "--csv", default="Game-of-life-migration/metrics/migration_metrics.csv"
    )
    parser.add_argument("--out-dir", default=None, help="Output directory for plots")
    parser.add_argument(
        "--run-id-prefix",
        default=None,
        help="Only include runs whose run_id starts with this prefix",
    )
    parser.add_argument(
        "--run-ids-file",
        default=None,
        help="Only include runs listed in this file (one run_id per line).",
    )
    parser.add_argument(
        "--node-metrics-dir", default="Game-of-life-migration/metrics/node_exporter"
    )

    parser.add_argument(
        "--profile-name",
        default="",
        help="Network profile name to append to plot titles",
    )
    parser.add_argument(
        "--resource-transfer-mode",
        choices=["host", "direct"],
        default=None,
        help="Restrict node-exporter resource plots to one transfer mode.",
    )

    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else (Path("Game-of-life-migration/metrics/plots"))
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    filtered_csv = out_dir / "filtered_migration_metrics.csv"
    df = load_migration_csv(str(csv_path))
    run_ids = None
    if args.run_ids_file:
        p = Path(args.run_ids_file)
        if p.exists():
            run_ids = {
                line.strip()
                for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            if not run_ids:
                run_ids = None
    if args.run_id_prefix:
        df = df[df["run_id"].astype(str).str.startswith(args.run_id_prefix)].copy()
    if run_ids:
        df = df[df["run_id"].astype(str).isin(run_ids)].copy()
    if df.empty:
        print("No rows selected for plotting (empty metrics after filtering).")
        return 0
    df.to_csv(filtered_csv, index=False)
    successful_runs_only(df).to_csv(
        out_dir / "successful_migration_metrics.csv", index=False
    )

    plot_downtime(
        str(filtered_csv),
        str(out_dir / "downtime_comparison.png"),
        title_suffix=args.profile_name,
    )
    plot_phase_breakdown(
        str(filtered_csv),
        str(out_dir / "phase_breakdown.png"),
        title_suffix=args.profile_name,
    )
    plot_transfer_analysis(
        str(filtered_csv),
        str(out_dir / "transfer_analysis.png"),
        title_suffix=args.profile_name,
    )
    plot_transfer_phase_breakdown(
        str(filtered_csv),
        str(out_dir / "transfer_phase_breakdown.png"),
        title_suffix=args.profile_name,
    )
    plot_node_exporter_summary(
        str(filtered_csv),
        args.node_metrics_dir,
        str(out_dir / "node_exporter_summary.png"),
        run_id_prefix=args.run_id_prefix,
        run_ids=run_ids,
        transfer_mode=args.resource_transfer_mode,
    )

    print(f"✓ Plots written to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
