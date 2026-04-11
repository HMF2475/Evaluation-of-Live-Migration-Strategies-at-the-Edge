#!/usr/bin/env python3
"""
Benchmark runner for CRIU TCP client migration.

This is the "network live-migration" benchmark: the workload is a TCP *client*
with an established connection.

Important: the workload must bind to a VIP and the VIP is moved source->dest
between dump and restore (see `Network-live-migration/CRIU-limitations.md`).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from .multipass_command import MultipassCommand
    from .metrics import MigrationMetrics
    from .tcp_client_migration import (
        TcpClientColdMigration,
        TcpClientPrecopyMigration,
        TcpClientPostcopyMigration,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from multipass_command import MultipassCommand
    from metrics import MigrationMetrics
    from tcp_client_migration import (
        TcpClientColdMigration,
        TcpClientPrecopyMigration,
        TcpClientPostcopyMigration,
    )


def get_csv_path() -> Path:
    script_dir = Path(__file__).resolve().parent
    csv_path = script_dir.parent.parent / "metrics" / "migration_metrics.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    return csv_path


def write_metrics_to_csv(metrics: MigrationMetrics, csv_path: Path) -> None:
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "run_id",
                "technology",
                "migration_method",
                "network_migration",
                "checkpoint_ms",
                "archive_bytes",
                "transfer_ms",
                "restore_ms",
                "downtime_ms",
                "bandwidth_mbps",
                "src_arch",
                "dst_arch",
                "same_arch",
                "success",
                "notes",
                "timestamp",
            ],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "run_id": metrics.run_id,
                "technology": metrics.technology,
                "migration_method": metrics.migration_method,
                "network_migration": metrics.network_migration,
                "checkpoint_ms": metrics.checkpoint_ms,
                "archive_bytes": metrics.archive_bytes,
                "transfer_ms": metrics.transfer_ms,
                "restore_ms": metrics.restore_ms,
                "downtime_ms": metrics.downtime_ms,
                "bandwidth_mbps": f"{metrics.bandwidth_mbps:.2f}",
                "src_arch": metrics.src_arch,
                "dst_arch": metrics.dst_arch,
                "same_arch": metrics.same_arch,
                "success": metrics.success,
                "notes": metrics.notes,
                "timestamp": metrics.timestamp,
            }
        )


_RUN_ID_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{4})-(?P<mode>host|direct)-(?P<strategy>cold|precopy|postcopy)-(?P<num>\d{4})$"
)


def _next_run_number(csv_path: Path, *, date: str, mode: str, strategy: str) -> int:
    if not csv_path.exists():
        return 0
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
        return 0
    return max_n + 1


def main() -> int:
    parser = argparse.ArgumentParser(description="CRIU TCP client migration benchmark")
    parser.add_argument(
        "strategy", choices=["cold", "precopy", "postcopy"], help="Migration strategy"
    )
    parser.add_argument(
        "--source", required=True, help="Source node (client runs here initially)"
    )
    parser.add_argument(
        "--dest", required=True, help="Destination node (client restored here)"
    )
    parser.add_argument(
        "--server", default=None, help="Server node (optional, used for validation/ARP)"
    )
    parser.add_argument(
        "--transfer-mode",
        choices=["host", "direct"],
        default="host",
        help="Archive transfer mode: host (via relay/host) or direct (scp)",
    )
    parser.add_argument(
        "--relay-node", default=None, help="Relay node used for host-mode transfers"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=2,
        help="Precopy: number of pre-dump iterations",
    )
    parser.add_argument(
        "--page-server-port",
        type=int,
        default=9999,
        help="Postcopy: lazy-pages port on source",
    )
    parser.add_argument("--run-id", default=None, help="Custom run ID")
    parser.add_argument(
        "--csv",
        default=None,
        help="CSV path (default: Network-live-migration/metrics/migration_metrics.csv)",
    )

    args = parser.parse_args()

    csv_path = Path(args.csv) if args.csv else get_csv_path()
    if args.run_id is None:
        date_str = datetime.now().strftime("%d-%m-%Y")
        n = _next_run_number(
            csv_path, date=date_str, mode=args.transfer_mode, strategy=args.strategy
        )
        args.run_id = f"{date_str}-{args.transfer_mode}-{args.strategy}-{n:04d}"

    source = MultipassCommand(args.source)
    dest = MultipassCommand(args.dest)
    server = MultipassCommand(args.server) if args.server else None

    print(f"[{datetime.now().strftime('%H:%M:%S')}] === TCP CLIENT MIGRATION ===")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Run ID: {args.run_id}")
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Source: {args.source}, Dest: {args.dest}"
    )
    if args.server:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Server: {args.server}")
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] Transfer mode: {args.transfer_mode}"
    )
    if args.relay_node:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Relay node: {args.relay_node}")
    print()

    if args.strategy == "cold":
        strategy = TcpClientColdMigration(
            source,
            dest,
            server=server,
            transfer_mode=args.transfer_mode,
            relay_node=args.relay_node,
        )
    elif args.strategy == "precopy":
        strategy = TcpClientPrecopyMigration(
            source,
            dest,
            server=server,
            transfer_mode=args.transfer_mode,
            relay_node=args.relay_node,
            iterations=args.iterations,
        )
    else:
        strategy = TcpClientPostcopyMigration(
            source,
            dest,
            server=server,
            transfer_mode=args.transfer_mode,
            relay_node=args.relay_node,
            page_server_port=args.page_server_port,
        )

    ok = strategy.migrate(args.run_id)
    strategy.finalize_metrics()
    write_metrics_to_csv(strategy.metrics, csv_path)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Metrics saved to {csv_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
