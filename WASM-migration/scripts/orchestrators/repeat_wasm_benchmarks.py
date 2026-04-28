#!/usr/bin/env python3
"""Repeat WASM edge-node migrations and append CRIU-compatible metrics.

This wrapper mirrors the CRIU repeat runners, but WASM currently has only one
strategy for checkpoint/restore. It creates batch run IDs, optionally captures
node_exporter snapshots, calls `wasm_benchmark.py` for each run, and generates
plots for the finished batch.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

try:
    from .node_exporter_metrics import append_node_exporter_row
except ImportError:
    from node_exporter_metrics import append_node_exporter_row


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def wasm_root() -> Path:
    return repo_root() / "WASM-migration"


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
        sys.stdout.flush()
        log_file.write(line)
        log_file.flush()
    proc.wait()
    log_file.write(f"[exit={proc.returncode}]\n")
    log_file.flush()
    return int(proc.returncode)


def snapshot_node_exporter(
    node: str, out_path: Path, meta_path: Optional[Path] = None
) -> bool:
    """Capture raw Prometheus text from node_exporter inside one Multipass node."""
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
    out_path.write_text(result.stdout, encoding="utf-8")
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
            ),
            encoding="utf-8",
        )
    return True


def iter_runs(host_runs: int, direct_runs: int) -> Iterable[tuple[str, int]]:
    """Yield transfer modes in stable order so CSV/plots are predictable."""
    for i in range(1, host_runs + 1):
        yield "host", i
    for i in range(1, direct_runs + 1):
        yield "direct", i


_RUN_ID_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{4})-(?P<mode>host|direct)-(?P<strategy>cold)-(?P<num>\d{4})$"
)


def _max_existing_run_number(
    csv_path: Path, *, date: str, mode: str, strategy: str
) -> int:
    """Continue run numbering for same day/mode instead of overwriting IDs."""
    if not csv_path.exists():
        return -1
    max_n = -1
    try:
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                match = _RUN_ID_RE.match(str(row.get("run_id", "")).strip())
                if not match:
                    continue
                if (
                    match.group("date") != date
                    or match.group("mode") != mode
                    or match.group("strategy") != strategy
                ):
                    continue
                max_n = max(max_n, int(match.group("num")))
    except Exception:
        return -1
    return max_n


def run_repeats(
    *,
    source: str,
    dest: str,
    relay_node: Optional[str],
    host_runs: int,
    direct_runs: int,
    module: Path,
    commands_dir: Path,
    profile_name: str,
    date_str: str,
    csv_path: Path,
    run_ids_out: list[str],
    log_file,
    root: Path,
    warmup_seconds: float,
    timeout_seconds: float,
    snapshot_node_metrics: bool,
    continue_on_failure: bool,
    skip_deploy: bool,
) -> int:
    benchmark = (
        root / "WASM-migration" / "scripts" / "orchestrators" / "wasm_benchmark.py"
    )
    metrics_dir = root / "WASM-migration" / "metrics" / "node_exporter"
    node_csv = root / "WASM-migration" / "metrics" / "node_exporter_metrics.csv"

    next_n_by_mode = {
        "host": _max_existing_run_number(
            csv_path, date=date_str, mode="host", strategy="cold"
        )
        + 1,
        "direct": _max_existing_run_number(
            csv_path, date=date_str, mode="direct", strategy="cold"
        )
        + 1,
    }

    any_failures = False
    for mode, _idx in iter_runs(host_runs, direct_runs):
        run_n = next_n_by_mode[mode]
        next_n_by_mode[mode] = run_n + 1
        run_id = f"{date_str}-{mode}-cold-{run_n:04d}"
        run_ids_out.append(run_id)
        print(f"\n=== RUN {run_id} ===\n")

        if snapshot_node_metrics:
            # Before/after snapshots let visualization summarize CPU, memory,
            # and disk IO around the whole migration window.
            for node, suffix in ((source, "before"), (dest, "before")):
                ok = snapshot_node_exporter(
                    node,
                    metrics_dir / run_id / f"{node}-{suffix}.prom",
                    metrics_dir / run_id / f"{node}-{suffix}.json",
                )
                if not ok:
                    print(f"WARNING: node_exporter snapshot failed: {node} ({suffix})")

        cmd = [
            "python3",
            str(benchmark),
            "--source",
            source,
            "--dest",
            dest,
            "--transfer-mode",
            mode,
            "--run-id",
            run_id,
            "--module",
            str(module),
            "--commands-dir",
            str(commands_dir),
            "--csv",
            str(csv_path),
            "--profile-name",
            profile_name,
            "--warmup-seconds",
            str(warmup_seconds),
            "--timeout-seconds",
            str(timeout_seconds),
        ]
        if relay_node and mode == "host":
            cmd += ["--relay-node", relay_node]
        if skip_deploy:
            cmd += ["--skip-deploy"]

        rc = run_and_tee(cmd, log_file, cwd=root)
        if rc != 0:
            any_failures = True
            log_file.write(f"[repeat] ERROR: benchmark failed for run_id={run_id}\n")
            log_file.flush()
            if not continue_on_failure:
                return rc

        if snapshot_node_metrics:
            for node, suffix in ((source, "after"), (dest, "after")):
                ok = snapshot_node_exporter(
                    node,
                    metrics_dir / run_id / f"{node}-{suffix}.prom",
                    metrics_dir / run_id / f"{node}-{suffix}.json",
                )
                if not ok:
                    print(f"WARNING: node_exporter snapshot failed: {node} ({suffix})")

            append_node_exporter_row(
                node_csv,
                run_id=run_id,
                migration_method="cold",
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

    return 1 if any_failures else 0


def generate_plots(
    *,
    root: Path,
    csv_path: Path,
    run_ids_path: Path,
    base_run_id: str,
    profile_name: str,
) -> None:
    """Render same plot family as CRIU, filtered to this batch's run IDs."""
    out_dir = root / "WASM-migration" / "metrics" / "plots" / base_run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    viz = (
        root / "WASM-migration" / "scripts" / "visualization" / "generate_all_plots.py"
    )
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
            str(root / "WASM-migration" / "metrics" / "node_exporter"),
            "--profile-name",
            profile_name,
        ],
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repeat Tinto WASM migrations between edge nodes."
    )
    parser.add_argument("strategy", choices=["cold", "suite"], default="cold")
    parser.add_argument(
        "--strategies",
        default=None,
        help="Accepted for parity; only cold is implemented.",
    )
    parser.add_argument("--source", default="edge-node-1")
    parser.add_argument("--dest", default="edge-node-2")
    parser.add_argument("--relay-node", default=None)
    parser.add_argument("--host-runs", type=int, default=1)
    parser.add_argument("--direct-runs", type=int, default=1)
    parser.add_argument(
        "--module",
        type=Path,
        default=wasm_root()
        / "wasm-migrate-commands"
        / "wasm_test_computation"
        / "3mm_with_cr.wasm",
    )
    parser.add_argument(
        "--commands-dir",
        type=Path,
        default=wasm_root() / "wasm-migrate-commands" / "build",
    )
    parser.add_argument(
        "--csv", type=Path, default=wasm_root() / "metrics" / "migration_metrics.csv"
    )
    parser.add_argument("--profile-name", default="")
    parser.add_argument("--base-run-id", default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--warmup-seconds", type=float, default=0.01)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--snapshot-node-metrics", action="store_true")
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--skip-deploy", action="store_true")
    parser.add_argument(
        "--no-plots", action="store_true", help="Skip plot generation at the end."
    )
    args = parser.parse_args()

    strategies = [
        s.strip() for s in (args.strategies or "cold").split(",") if s.strip()
    ]
    unsupported = [s for s in strategies if s != "cold"]
    if unsupported:
        print(
            f"ERROR: WASM runner only supports cold/checkpoint migration, got: {unsupported}"
        )
        return 2

    root = repo_root()
    date_str = datetime.now().strftime("%d-%m-%Y")
    time_str = datetime.now().strftime("%H%M%S")
    base_run_id = args.base_run_id or (
        f"{date_str}-wasm-cold-h{args.host_runs}-d{args.direct_runs}-"
        f"{'snap' if args.snapshot_node_metrics else 'nosnap'}-{time_str}"
    )

    log_dir = root / "WASM-migration" / "metrics" / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log_file) if args.log_file else log_dir / f"{base_run_id}.log"
    run_ids_path = log_path.with_suffix(".run_ids.txt")

    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"=== repeat_wasm_benchmarks.py {datetime.now().isoformat()} ===\n")
        lf.write(
            f"base_run_id={base_run_id} source={args.source} dest={args.dest} "
            f"host_runs={args.host_runs} direct_runs={args.direct_runs} relay_node={args.relay_node} "
            f"profile_name={args.profile_name} module={args.module} snapshot_node_metrics={args.snapshot_node_metrics}\n"
        )
        lf.flush()
        run_ids: list[str] = []
        rc = run_repeats(
            source=args.source,
            dest=args.dest,
            relay_node=args.relay_node,
            host_runs=args.host_runs,
            direct_runs=args.direct_runs,
            module=args.module,
            commands_dir=args.commands_dir,
            profile_name=args.profile_name,
            date_str=date_str,
            csv_path=args.csv,
            run_ids_out=run_ids,
            log_file=lf,
            root=root,
            warmup_seconds=args.warmup_seconds,
            timeout_seconds=args.timeout_seconds,
            snapshot_node_metrics=args.snapshot_node_metrics,
            continue_on_failure=args.continue_on_failure,
            skip_deploy=args.skip_deploy,
        )
        if run_ids:
            run_ids_path.write_text("\n".join(run_ids) + "\n", encoding="utf-8")
            lf.write(f"[repeat] run_ids_file={run_ids_path}\n")
        print(f"\nAll runs complete. Log: {log_path}")
        if run_ids:
            print(f"Run IDs: {run_ids_path}")
        if rc == 0 and not args.no_plots and run_ids:
            generate_plots(
                root=root,
                csv_path=args.csv,
                run_ids_path=run_ids_path,
                base_run_id=base_run_id,
                profile_name=args.profile_name,
            )
        return rc


if __name__ == "__main__":
    raise SystemExit(main())
