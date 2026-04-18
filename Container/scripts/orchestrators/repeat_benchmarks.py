#!/usr/bin/env python3
"""
Repeatable benchmark runner for native CRIU orchestrators.

Runs N migrations in host transfer mode and N in direct transfer mode, resetting
nodes and restarting the workload between runs. Optionally snapshots
node_exporter metrics before/after each migration.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import json
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

try:
    from .node_exporter_metrics import append_node_exporter_row
except ImportError:
    from node_exporter_metrics import append_node_exporter_row


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def run_and_tee(cmd: list[str], log_file, *, cwd: Optional[Path] = None) -> int:
    log_file.write(f"\n$ {' '.join(cmd)}\n")
    log_file.flush()

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        log_file.write(line)
    proc.wait()
    log_file.write(f"[exit={proc.returncode}]\n")
    log_file.flush()
    return int(proc.returncode)


def snapshot_node_exporter(
    node: str, out_path: Path, meta_path: Optional[Path] = None
) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t_host = time.time()
    result = subprocess.run(
        [
            "multipass",
            "exec",
            node,
            "--",
            "bash",
            "-lc",
            "curl -fsS http://127.0.0.1:9100/metrics",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    out_path.write_text(result.stdout)
    if meta_path:
        node_time = None
        for line in result.stdout.splitlines():
            if line.startswith("node_time_seconds "):
                try:
                    node_time = float(line.split()[1])
                except (ValueError, IndexError):
                    node_time = None
                break
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(
                {
                    "node": node,
                    "captured_at_host_epoch": t_host,
                    "captured_at_node_time_seconds": node_time,
                },
                indent=2,
                sort_keys=True,
            )
        )
    return True


def iter_runs(host_runs: int, direct_runs: int) -> Iterable[tuple[str, int]]:
    for i in range(1, host_runs + 1):
        yield "host", i
    for i in range(1, direct_runs + 1):
        yield "direct", i


_RUN_ID_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{4})-(?P<mode>host|direct)-(?P<strategy>cold|precopy|postcopy)-(?P<num>\d{4})$"
)


def _max_existing_run_number(
    csv_path: Path, *, date: str, mode: str, strategy: str
) -> int:
    """
    Return the max NNNN for run_ids matching: DD-MM-YYYY-mode-strategy-NNNN.

    If no runs match, returns -1.
    """
    if not csv_path.exists():
        return -1

    max_n = -1
    try:
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                run_id = str(row.get("run_id", "")).strip()
                m = _RUN_ID_RE.match(run_id)
                if not m:
                    continue
                if (
                    m.group("date") != date
                    or m.group("mode") != mode
                    or m.group("strategy") != strategy
                ):
                    continue
                try:
                    n = int(m.group("num"))
                except ValueError:
                    continue
                if n > max_n:
                    max_n = n
    except Exception:
        return -1

    return max_n


def run_strategy(
    *,
    strategy: str,
    source: str,
    dest: str,
    host_runs: int,
    direct_runs: int,
    iterations: int,
    page_server_port: int,
    relay_node: Optional[str],
    profile_name: str,
    date_str: str,
    csv: Optional[str],
    csv_path: Path,
    run_ids_out: list[str],
    log_file,
    root: Path,
    warmup_seconds: int,
    snapshot_node_metrics: bool,
    continue_on_failure: bool,
) -> int:
    reset_script = root / "Container" / "scripts" / "setup" / "reset_nodes.py"
    benchmark = root / "Container" / "scripts" / "orchestrators" / "criu_benchmark.py"
    metrics_dir = root / "Container" / "metrics" / "node_exporter"
    node_csv = root / "Container" / "metrics" / "node_exporter_metrics.csv"
    workloads_dir = root / "Container" / "scripts" / "workloads"

    start_cmd = ["bash", str(workloads_dir / "start_counter_c.sh"), source]

    next_n_by_mode = {
        "host": _max_existing_run_number(
            csv_path, date=date_str, mode="host", strategy=strategy
        )
        + 1,
        "direct": _max_existing_run_number(
            csv_path, date=date_str, mode="direct", strategy=strategy
        )
        + 1,
    }

    for mode, idx in iter_runs(host_runs, direct_runs):
        run_n = next_n_by_mode[mode]
        next_n_by_mode[mode] = run_n + 1
        run_id = f"{date_str}-{mode}-{strategy}-{run_n:04d}"
        run_ids_out.append(run_id)
        print(f"\n=== RUN {run_id} ===\n")

        rc = run_and_tee(
            ["python3", str(reset_script), source, dest], log_file, cwd=root
        )
        if rc != 0 and not continue_on_failure:
            return rc

        # Workload start happens on the SOURCE node before each migration.
        rc = run_and_tee(start_cmd, log_file, cwd=root)
        if rc != 0 and not continue_on_failure:
            return rc

        if warmup_seconds > 0:
            log_file.write(f"[repeat] warmup {warmup_seconds}s\n")
            log_file.flush()
            time.sleep(warmup_seconds)

        if snapshot_node_metrics:
            ok = snapshot_node_exporter(
                source,
                metrics_dir / run_id / f"{source}-before.prom",
                metrics_dir / run_id / f"{source}-before.json",
            )
            if not ok:
                print(f"WARNING: node_exporter snapshot failed: {source} (before)")
                log_file.write(
                    f"[repeat] WARNING: node_exporter snapshot failed: {source} (before)\n"
                )
                log_file.flush()

            ok = snapshot_node_exporter(
                dest,
                metrics_dir / run_id / f"{dest}-before.prom",
                metrics_dir / run_id / f"{dest}-before.json",
            )
            if not ok:
                print(f"WARNING: node_exporter snapshot failed: {dest} (before)")
                log_file.write(
                    f"[repeat] WARNING: node_exporter snapshot failed: {dest} (before)\n"
                )
                log_file.flush()

        cmd = [
            "python3",
            str(benchmark),
            strategy,
            "--source",
            source,
            "--dest",
            dest,
            "--transfer-mode",
            mode,
            "--run-id",
            run_id,
        ]
        if relay_node and mode == "host":
            cmd += ["--relay-node", relay_node]
        if strategy == "precopy":
            cmd += ["--iterations", str(iterations)]
        if csv:
            cmd += ["--csv", csv]
        if strategy == "postcopy":
            cmd += ["--page-server-port", str(page_server_port)]

        if profile_name:
            cmd += ["--profile-name", profile_name]

        rc = run_and_tee(cmd, log_file, cwd=root)
        if rc != 0 and not continue_on_failure:
            return rc

        if snapshot_node_metrics:
            ok = snapshot_node_exporter(
                source,
                metrics_dir / run_id / f"{source}-after.prom",
                metrics_dir / run_id / f"{source}-after.json",
            )
            if not ok:
                print(f"WARNING: node_exporter snapshot failed: {source} (after)")
                log_file.write(
                    f"[repeat] WARNING: node_exporter snapshot failed: {source} (after)\n"
                )
                log_file.flush()

            ok = snapshot_node_exporter(
                dest,
                metrics_dir / run_id / f"{dest}-after.prom",
                metrics_dir / run_id / f"{dest}-after.json",
            )
            if not ok:
                print(f"WARNING: node_exporter snapshot failed: {dest} (after)")
                log_file.write(
                    f"[repeat] WARNING: node_exporter snapshot failed: {dest} (after)\n"
                )
                log_file.flush()

            saved = append_node_exporter_row(
                node_csv,
                run_id=run_id,
                migration_method=strategy,
                transfer_mode=mode,
                source_node=source,
                dest_node=dest,
                src_before_prom=metrics_dir / run_id / f"{source}-before.prom",
                src_after_prom=metrics_dir / run_id / f"{source}-after.prom",
                src_before_meta=metrics_dir / run_id / f"{source}-before.json",
                src_after_meta=metrics_dir / run_id / f"{source}-after.json",
                dst_before_prom=metrics_dir / run_id / f"{dest}-before.prom",
                dst_after_prom=metrics_dir / run_id / f"{dest}-after.prom",
                dst_before_meta=metrics_dir / run_id / f"{dest}-before.json",
                dst_after_meta=metrics_dir / run_id / f"{dest}-after.json",
            )
            if not saved:
                print("WARNING: node_exporter CSV row not written (missing snapshots)")
                log_file.write(
                    "[repeat] WARNING: node_exporter CSV row not written (missing snapshots)\n"
                )
                log_file.flush()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repeat CRIU migrations and store logs."
    )
    parser.add_argument("strategy", choices=["cold", "precopy", "postcopy", "suite"])
    parser.add_argument(
        "--strategies",
        default=None,
        help="Comma-separated strategy list overriding positional (e.g. cold,precopy,postcopy)",
    )
    parser.add_argument("--source", default="edge-node-1")
    parser.add_argument("--dest", default="edge-node-2")
    parser.add_argument("--host-runs", type=int, default=1)
    parser.add_argument("--direct-runs", type=int, default=1)
    parser.add_argument(
        "--iterations", type=int, default=2, help="Pre-dump iterations (precopy only)"
    )
    parser.add_argument(
        "--page-server-port",
        type=int,
        default=9999,
        help="Postcopy only: page-server TCP port",
    )
    parser.add_argument(
        "--relay-node",
        default=None,
        help="Optional relay VM for host-mode transfers (for example: edge-host-1)",
    )
    parser.add_argument(
        "--profile-name",
        default="",
        help="Optional profile name for experiment tracking (passed to orchestrator)",
    )
    parser.add_argument("--base-run-id", default=None)
    parser.add_argument("--csv", default=None, help="CSV output path (optional)")
    parser.add_argument(
        "--log-file", default=None, help="Append all output to this file (optional)"
    )
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--snapshot-node-metrics", action="store_true")
    parser.add_argument(
        "--warmup-seconds",
        type=int,
        default=2,
        help="Wait after starting workload before dumping",
    )
    parser.add_argument(
        "--no-plots", action="store_true", help="Skip plot generation at the end"
    )

    args = parser.parse_args()

    root = repo_root()

    date_str = datetime.now().strftime("%d-%m-%Y")
    time_str = datetime.now().strftime("%H%M%S")

    if args.strategies:
        strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    else:
        strategies = (
            ["cold", "precopy"] if args.strategy == "suite" else [args.strategy]
        )

    def auto_base_run_id() -> str:
        # Meaningful batch name for logs/plots (run_ids inside the CSV are independent).
        # Example:
        #   31-03-2026-counter-cold-precopy-postcopy-h30-d30-i5-netno-snap-235124
        strat_part = "-".join(strategies)
        snap_part = "snap" if args.snapshot_node_metrics else "nosnap"
        plots_part = "noplots" if args.no_plots else "plots"
        it_part = f"i{args.iterations}" if "precopy" in strategies else "i0"
        relay_part = f"-relay-{args.relay_node}" if args.relay_node else ""
        return (
            f"{date_str}-counter-{strat_part}-h{args.host_runs}-d{args.direct_runs}-"
            f"{it_part}{relay_part}-{snap_part}-{plots_part}-{time_str}"
        )

    base_run_id = args.base_run_id or auto_base_run_id()

    log_dir = root / "Container" / "metrics" / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (
        Path(args.log_file) if args.log_file else (log_dir / f"{base_run_id}.log")
    )
    run_ids_path = log_path.with_suffix(".run_ids.txt")

    csv_path = (
        Path(args.csv)
        if args.csv
        else (root / "Container" / "metrics" / "migration_metrics.csv")
    )

    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"=== repeat_benchmarks.py {datetime.now().isoformat()} ===\n")
        lf.write(f"base_run_id={base_run_id}\n")
        lf.write(
            f"strategies={','.join(strategies)} workload=counter "
            f"source={args.source} dest={args.dest} host_runs={args.host_runs} direct_runs={args.direct_runs} "
            f"iterations={args.iterations} relay_node={args.relay_node} profile_name={args.profile_name} "
            f"snapshot_node_metrics={args.snapshot_node_metrics} no_plots={args.no_plots}\n"
        )
        lf.flush()

        batch_run_ids: list[str] = []
        for strategy in strategies:
            rc = run_strategy(
                strategy=strategy,
                source=args.source,
                dest=args.dest,
                host_runs=args.host_runs,
                direct_runs=args.direct_runs,
                iterations=args.iterations,
                page_server_port=args.page_server_port,
                relay_node=args.relay_node,
                date_str=date_str,
                profile_name=args.profile_name,
                csv=args.csv,
                csv_path=csv_path,
                run_ids_out=batch_run_ids,
                log_file=lf,
                root=root,
                warmup_seconds=args.warmup_seconds,
                snapshot_node_metrics=args.snapshot_node_metrics,
                continue_on_failure=args.continue_on_failure,
            )
            if rc != 0:
                return rc

        print(f"\nAll runs complete. Log: {log_path}")
        if batch_run_ids:
            run_ids_path.write_text("\n".join(batch_run_ids) + "\n", encoding="utf-8")
            lf.write(f"[repeat] run_ids_file={run_ids_path}\n")
            lf.flush()

    if not args.no_plots:
        out_dir = root / "Container" / "metrics" / "plots" / base_run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        viz = root / "Container" / "scripts" / "visualization" / "generate_all_plots.py"
        subprocess.run(
            [
                "python3",
                str(viz),
                "--csv",
                str(csv_path),
                "--run-ids-file",
                str(run_ids_path),
                "--out-dir",
                str(out_dir),
                "--node-metrics-dir",
                str(root / "Container" / "metrics" / "node_exporter"),
                "--profile-name",
                args.profile_name,
            ],
            check=False,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
