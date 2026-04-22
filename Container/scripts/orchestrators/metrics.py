"""
Migration metrics data structures.

Unified CSV schema for storing benchmark results across all migration methods.
"""

from dataclasses import dataclass, asdict


@dataclass
class MigrationMetrics:
    """Container for migration benchmark results.

    Unified 16-column CSV schema compatible across all migration types.
    """

    run_id: str
    technology: str = "CRIU"
    migration_method: str = "cold"  # cold, pre-copy, post-copy, hybrid
    network_migration: str = "no"  # compatibility field retained for merged plotting
    checkpoint_ms: int = 0
    archive_bytes: int = 0
    transfer_ms: int = 0
    restore_ms: int = 0
    downtime_ms: int = 0
    bandwidth_mbps: float = 0.0
    src_arch: str = ""
    dst_arch: str = ""
    same_arch: bool = False
    success: bool = False
    notes: str = ""
    timestamp: str = ""
    profile_name: str = ""

    # Additional metrics for detailed analysis
    final_dump_ms: int = 0  # Time to perform final dump (freeze duration)
    predump_ms: int = 0  # Total time spent on pre-dumps
    total_ms: int = 0  # End-to-end wall time for the run (best-effort)
    lazy_pages_active_ms: int = 0  # Postcopy: time lazy-pages was active (best-effort)
    lazy_pages_log_bytes: int = 0  # Postcopy: size of lazy-pages log (best-effort)


def metrics_to_dict(metrics: MigrationMetrics) -> dict:
    """Convert metrics to dictionary for CSV output."""
    return asdict(metrics)


def get_csv_header() -> list:
    """Get CSV column names in order."""
    return [
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
        "profile_name",
        "predump_ms",
        "final_dump_ms",
        "total_ms",
        "lazy_pages_active_ms",
        "lazy_pages_log_bytes",
    ]
