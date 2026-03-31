#!/usr/bin/env python3
"""
CRIU Migration Benchmark Framework - Refactored with Strategy Pattern

Uses modular strategy classes for cold/precopy/postcopy migrations.
This provides better code organization, testability, and correct downtime calculation.

Usage:
    python3 -m orchestrators cold --source edge-node-1 --dest edge-node-2
    python3 -m orchestrators precopy --source edge-node-1 --dest edge-node-2 --iterations 2
    python3 -m orchestrators postcopy --source edge-node-1 --dest edge-node-2

Or run the script directly:
    python3 scripts/orchestrators/criu_benchmark.py cold --source edge-node-1 --dest edge-node-2

Results are appended to Container/metrics/migration_metrics.csv
"""

import argparse
import sys
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import with try/except to support both direct script execution and module import
try:
    from .multipass_command import MultipassCommand
    from .metrics import MigrationMetrics
    from .cold_migration import ColdMigration
    from .precopy_migration import PrecopyMigration
    from .postcopy_migration import PostcopyMigration
except ImportError:
    # Direct script execution: add parent directory to path
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from multipass_command import MultipassCommand
    from metrics import MigrationMetrics
    from cold_migration import ColdMigration
    from precopy_migration import PrecopyMigration
    from postcopy_migration import PostcopyMigration


def get_csv_path() -> Path:
    """Get the CSV metrics file path."""
    script_dir = Path(__file__).resolve().parent
    csv_path = script_dir.parent.parent / "metrics" / "migration_metrics.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    return csv_path


def write_metrics_to_csv(metrics: MigrationMetrics, csv_path: Path):
    """Append migration metrics to CSV file."""
    file_exists = csv_path.exists()
    
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "run_id", "technology", "migration_method", "network_migration",
                "checkpoint_ms", "archive_bytes", "transfer_ms", "restore_ms",
                "downtime_ms", "bandwidth_mbps", "src_arch", "dst_arch",
                "same_arch", "success", "notes", "timestamp"
            ]
        )
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow({
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
        })

_RUN_ID_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{4})-(?P<mode>host|direct)-(?P<strategy>cold|precopy|postcopy)-(?P<num>\d{4})$"
)


def _next_run_number(csv_path: Path, *, date: str, mode: str, strategy: str) -> int:
    """Return next NNNN for the run_id scheme: DD-MM-YYYY-mode-strategy-NNNN."""
    if not csv_path.exists():
        return 0
    max_n = -1
    try:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                run_id = str(row.get("run_id", "")).strip()
                m = _RUN_ID_RE.match(run_id)
                if not m:
                    continue
                if m.group("date") != date or m.group("mode") != mode or m.group("strategy") != strategy:
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


def main():
    parser = argparse.ArgumentParser(
        description="CRIU Migration Benchmark Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/criu_benchmark.py cold --source edge-node-1 --dest edge-node-2
  python3 scripts/criu_benchmark.py precopy --source edge-node-1 --dest edge-node-2 --iterations 2
  python3 scripts/criu_benchmark.py postcopy --source edge-node-1 --dest edge-node-2 (⚠️ EXPERIMENTAL)
        """
    )
    
    parser.add_argument(
        "strategy",
        choices=["cold", "precopy", "postcopy"],
        help="Migration strategy to test (postcopy is EXPERIMENTAL)"
    )
    
    parser.add_argument("--source", required=True, help="Source node name")
    parser.add_argument("--dest", required=True, help="Destination node name")
    parser.add_argument(
        "--run-id",
        default=None,
        help="Custom run ID (default: auto-generated from strategy and timestamp)"
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="CSV output file (default: Container/metrics/migration_metrics.csv)"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=2,
        help="Pre-dump iterations for precopy (default: 2)"
    )
    parser.add_argument(
        "--transfer-mode",
        choices=["host", "direct"],
        default="host",
        help="Archive transfer mode: host (source->host->dest) or direct (source->dest via scp)"
    )
    parser.add_argument(
        "--network-migration",
        choices=["no", "yes"],
        default="no",
        help="Enable CRIU network socket options (experimental; may require --ext-net-map)"
    )
    parser.add_argument(
        "--ext-net-map",
        default=None,
        help='Optional CRIU ext-net-map mapping, e.g. "10.0.0.1:10.0.0.2"'
    )
    parser.add_argument(
        "--page-server-port",
        type=int,
        default=9999,
        help="Postcopy only: TCP port for lazy-pages page-server (default: 9999)"
    )
    
    args = parser.parse_args()

    # Determine CSV path
    if args.csv:
        csv_path = Path(args.csv)
    else:
        csv_path = get_csv_path()

    # Generate run_id if not provided
    if args.run_id is None:
        date_str = datetime.now().strftime("%d-%m-%Y")
        n = _next_run_number(csv_path, date=date_str, mode=args.transfer_mode, strategy=args.strategy)
        args.run_id = f"{date_str}-{args.transfer_mode}-{args.strategy}-{n:04d}"
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting {args.strategy} migration...")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Source: {args.source}, Dest: {args.dest}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Transfer mode: {args.transfer_mode}")
    print()
    
    # Create appropriate strategy instance
    source = MultipassCommand(args.source)
    dest = MultipassCommand(args.dest)
    network_migration = args.network_migration == "yes"
    
    if args.strategy == "cold":
        strategy = ColdMigration(
            source,
            dest,
            transfer_mode=args.transfer_mode,
            network_migration=network_migration,
            ext_net_map=args.ext_net_map,
        )
    elif args.strategy == "precopy":
        strategy = PrecopyMigration(
            source,
            dest,
            transfer_mode=args.transfer_mode,
            iterations=args.iterations,
            network_migration=network_migration,
            ext_net_map=args.ext_net_map,
        )
    elif args.strategy == "postcopy":
        strategy = PostcopyMigration(
            source,
            dest,
            transfer_mode=args.transfer_mode,
            network_migration=network_migration,
            ext_net_map=args.ext_net_map,
            page_server_port=args.page_server_port,
        )
    else:
        print(f"ERROR: Unknown strategy {args.strategy}")
        sys.exit(1)
    
    # Execute migration
    success = strategy.migrate(args.run_id)
    
    # Finalize metrics
    strategy.finalize_metrics()
    
    # Write to CSV
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Metrics saved to {csv_path}")
    write_metrics_to_csv(strategy.metrics, csv_path)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
