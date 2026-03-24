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


def main():
    parser = argparse.ArgumentParser(
        description="CRIU Migration Benchmark Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/criu_benchmark.py cold --source edge-node-1 --dest edge-node-2
  python3 scripts/criu_benchmark.py precopy --source edge-node-1 --dest edge-node-2 --iterations 2
  python3 scripts/criu_benchmark.py postcopy --source edge-node-1 --dest edge-node-2 (⚠️ EXPERIMENTAL - NOT YET FUNCTIONAL)
        """
    )
    
    parser.add_argument(
        "strategy",
        choices=["cold", "precopy", "postcopy"],
        help="Migration strategy to test (postcopy is EXPERIMENTAL, not yet functional)"
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
    
    args = parser.parse_args()
    
    # Generate run_id if not provided
    if args.run_id is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.run_id = f"{args.strategy}-{timestamp}"
    
    # Determine CSV path
    if args.csv:
        csv_path = Path(args.csv)
    else:
        csv_path = get_csv_path()
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting {args.strategy} migration...")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Source: {args.source}, Dest: {args.dest}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Transfer mode: {args.transfer_mode}")
    print()
    
    # Create appropriate strategy instance
    source = MultipassCommand(args.source)
    dest = MultipassCommand(args.dest)
    
    if args.strategy == "cold":
        strategy = ColdMigration(source, dest, transfer_mode=args.transfer_mode)
    elif args.strategy == "precopy":
        strategy = PrecopyMigration(source, dest, transfer_mode=args.transfer_mode, iterations=args.iterations)
    elif args.strategy == "postcopy":
        strategy = PostcopyMigration(source, dest, transfer_mode=args.transfer_mode)
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
