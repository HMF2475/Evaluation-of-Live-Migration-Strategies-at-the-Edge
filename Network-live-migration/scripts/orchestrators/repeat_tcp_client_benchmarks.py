#!/usr/bin/env python3
"""
Repeatable benchmark runner for TCP client CRIU migrations.

Runs N migrations in host transfer mode and N in direct transfer mode,
resetting nodes and restarting the TCP workload between runs.

Outputs:
- Network-live-migration/metrics/migration_metrics.csv (same schema as Container)
- Network-live-migration/metrics/node_exporter_metrics.csv (optional)
- Network-live-migration/metrics/run_logs/<suite>.log
- Network-live-migration/metrics/plots/<suite>/*.png (optional)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

try:
    from .node_exporter_metrics import append_node_exporter_row
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from node_exporter_metrics import append_node_exporter_row


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def run_and_tee(
    cmd: list[str],
    log_file,
    *,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> int:
    log_file.write(f"\n$ {' '.join(cmd)}\n")
    log_file.flush()

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
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


def snapshot_node_exporter(node: str, out_path: Path, meta_path: Path) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
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
    out_path.write_text(result.stdout, encoding="utf-8")
    node_time = None
    for line in result.stdout.splitlines():
        if line.startswith("node_time_seconds "):
            try:
                node_time = float(line.split()[1])
            except Exception:
                node_time = None
            break
    meta_path.write_text(
        f'{{"node":"{node}","captured_at_host_epoch":{t_host},"captured_at_node_time_seconds":{node_time}}}\n',
        encoding="utf-8",
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


def remove_vip(node: str, vip: str) -> None:
    subprocess.run(
        [
            "multipass",
            "exec",
            node,
            "--",
            "bash",
            "-lc",
            (
                "iface=$(ip -o -4 addr show | awk -v vip='{vip}' '$4 ~ \"^\"vip\"/\" {{print $2; exit}}'); "
                'if [ -n "$iface" ]; then '
                'sudo ip addr del {vip}/32 dev "$iface" 2>/dev/null || '
                'sudo ip addr del {vip}/24 dev "$iface" 2>/dev/null || true; '
                "fi"
            ).format(vip=vip),
        ],
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repeat TCP client migration benchmarks"
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument(
        "--server", required=True, help="Server node running tcp server"
    )
    parser.add_argument(
        "--relay-node",
        default=None,
        help="Relay node for host-mode transfers (can be same as --server)",
    )
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--vip", default="10.22.132.250", help="Client VIP to move during migration"
    )
    parser.add_argument(
        "--strategies", nargs="+", default=["cold", "precopy", "postcopy"]
    )
    parser.add_argument("--iterations", type=int, default=2, help="Precopy iterations")
    parser.add_argument("--page-server-port", type=int, default=9999)
    parser.add_argument("--host-runs", type=int, default=3)
    parser.add_argument("--direct-runs", type=int, default=3)
    parser.add_argument("--warmup-seconds", type=int, default=1)
    parser.add_argument("--snapshot-node-metrics", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--continue-on-failure", action="store_true")

    parser.add_argument(
        "--profile-name",
        default="",
        help="Optional profile name for experiment tracking (passed to orchestrator)",
    )
    args = parser.parse_args()

    root = repo_root()
    reset_script = (
        root / "Network-live-migration" / "scripts" / "setup" / "reset_nodes.py"
    )
    start_server = (
        root
        / "Network-live-migration"
        / "scripts"
        / "workloads"
        / "start_tcp_server.sh"
    )
    start_client = (
        root
        / "Network-live-migration"
        / "scripts"
        / "workloads"
        / "start_tcp_client.sh"
    )
    benchmark = (
        root
        / "Network-live-migration"
        / "scripts"
        / "orchestrators"
        / "tcp_client_benchmark.py"
    )
    plots_script = (
        root
        / "Network-live-migration"
        / "scripts"
        / "visualization"
        / "generate_all_plots.py"
    )

    metrics_csv = root / "Network-live-migration" / "metrics" / "migration_metrics.csv"
    node_csv = root / "Network-live-migration" / "metrics" / "node_exporter_metrics.csv"
    node_snap_dir = root / "Network-live-migration" / "metrics" / "node_exporter"
    run_logs_dir = root / "Network-live-migration" / "metrics" / "run_logs"
    plots_dir = root / "Network-live-migration" / "metrics" / "plots"

    date_str = datetime.now().strftime("%d-%m-%Y")
    stamp = datetime.now().strftime("%H%M%S")
    strategies_label = "-".join(args.strategies)
    suite_id = (
        f"{date_str}-tcpclient-{strategies_label}-h{args.host_runs}-d{args.direct_runs}"
        f"-i{args.iterations}-vip{args.vip.replace('.', '')}-srv{args.server}"
        f"-{stamp}"
    )
    run_logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_logs_dir / f"{suite_id}.log"
    run_ids_path = run_logs_dir / f"{suite_id}.run_ids.txt"

    run_ids: list[str] = []
    any_failures = False

    with log_path.open("w", encoding="utf-8") as log_file:
        for strategy_name in args.strategies:
            next_n_by_mode = {
                "host": _max_existing_run_number(
                    metrics_csv, date=date_str, mode="host", strategy=strategy_name
                )
                + 1,
                "direct": _max_existing_run_number(
                    metrics_csv, date=date_str, mode="direct", strategy=strategy_name
                )
                + 1,
            }

            for mode, _idx in iter_runs(args.host_runs, args.direct_runs):
                n = next_n_by_mode[mode]
                next_n_by_mode[mode] = n + 1
                run_id = f"{date_str}-{mode}-{strategy_name}-{n:04d}"
                run_ids.append(run_id)
                print(f"\n=== RUN {run_id} ===\n")

                # Reset edge nodes
                rc = run_and_tee(
                    [
                        "python3",
                        str(reset_script),
                        args.source,
                        args.dest,
                        args.server,
                        args.vip,
                    ],
                    log_file,
                    cwd=root,
                )
                if rc != 0:
                    any_failures = True
                    if not args.continue_on_failure:
                        return rc
                    # Best-effort cleanup before skipping to next run.
                    remove_vip(args.source, args.vip)
                    remove_vip(args.dest, args.vip)
                    continue

                # Ensure VIP is not lingering on the wrong node from previous run
                remove_vip(args.source, args.vip)
                remove_vip(args.dest, args.vip)

                # Start server then client
                rc = run_and_tee(
                    ["bash", str(start_server), args.server, str(args.port)],
                    log_file,
                    cwd=root,
                )
                if rc != 0:
                    any_failures = True
                    if not args.continue_on_failure:
                        return rc
                    continue

                rc = run_and_tee(
                    [
                        "bash",
                        str(start_client),
                        args.source,
                        args.server,
                        str(args.port),
                    ],
                    log_file,
                    cwd=root,
                    env={**os.environ, "TCP_VIP": args.vip},
                )
                if rc != 0:
                    any_failures = True
                    if not args.continue_on_failure:
                        return rc
                    continue

                if args.warmup_seconds > 0:
                    time.sleep(args.warmup_seconds)

                # node_exporter snapshots (source/dest/server)
                if args.snapshot_node_metrics:
                    for node in (args.source, args.dest, args.server):
                        ok = snapshot_node_exporter(
                            node,
                            node_snap_dir / run_id / f"{node}-before.prom",
                            node_snap_dir / run_id / f"{node}-before.json",
                        )
                        if not ok:
                            print(
                                f"WARNING: node_exporter snapshot failed: {node} (before)"
                            )

                cmd = [
                    "python3",
                    str(benchmark),
                    strategy_name,
                    "--source",
                    args.source,
                    "--dest",
                    args.dest,
                    "--server",
                    args.server,
                    "--transfer-mode",
                    mode,
                    "--run-id",
                    run_id,
                    "--csv",
                    str(metrics_csv),
                ]
                if args.profile_name:
                    cmd += ["--profile-name", args.profile_name]
                if args.relay_node and mode == "host":
                    cmd += ["--relay-node", args.relay_node]
                if strategy_name == "precopy":
                    cmd += ["--iterations", str(args.iterations)]
                if strategy_name == "postcopy":
                    cmd += ["--page-server-port", str(args.page_server_port)]

                rc = run_and_tee(cmd, log_file, cwd=root)
                if rc != 0:
                    any_failures = True
                    if not args.continue_on_failure:
                        return rc
                    continue

                if args.snapshot_node_metrics:
                    for node in (args.source, args.dest, args.server):
                        ok = snapshot_node_exporter(
                            node,
                            node_snap_dir / run_id / f"{node}-after.prom",
                            node_snap_dir / run_id / f"{node}-after.json",
                        )
                        if not ok:
                            print(
                                f"WARNING: node_exporter snapshot failed: {node} (after)"
                            )

                    # Append CSV row (only source/dest for compatibility with existing node_exporter_metrics)
                    append_node_exporter_row(
                        node_csv,
                        run_id=run_id,
                        migration_method=strategy_name,
                        transfer_mode=mode,
                        source_node=args.source,
                        dest_node=args.dest,
                        src_before_prom=node_snap_dir
                        / run_id
                        / f"{args.source}-before.prom",
                        src_after_prom=node_snap_dir
                        / run_id
                        / f"{args.source}-after.prom",
                        src_before_meta=node_snap_dir
                        / run_id
                        / f"{args.source}-before.json",
                        src_after_meta=node_snap_dir
                        / run_id
                        / f"{args.source}-after.json",
                        dst_before_prom=node_snap_dir
                        / run_id
                        / f"{args.dest}-before.prom",
                        dst_after_prom=node_snap_dir
                        / run_id
                        / f"{args.dest}-after.prom",
                        dst_before_meta=node_snap_dir
                        / run_id
                        / f"{args.dest}-before.json",
                        dst_after_meta=node_snap_dir
                        / run_id
                        / f"{args.dest}-after.json",
                    )

        run_ids_path.write_text("\n".join(run_ids) + "\n", encoding="utf-8")

    print(f"\nAll runs complete. Log: {log_path}")

    if not args.no_plots:
        out_dir = plots_dir / suite_id
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "python3",
                str(plots_script),
                "--csv",
                str(metrics_csv),
                "--out-dir",
                str(out_dir),
                "--run-ids-file",
                str(run_ids_path),
                "--node-metrics-dir",
                str(node_snap_dir),
                "--profile-name",
                args.profile_name,
            ],
            check=False,
        )

    return 1 if any_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
