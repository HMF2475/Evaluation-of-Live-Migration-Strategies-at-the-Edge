"""WASM migration metrics using the same CSV schema as CRIU experiments."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class MigrationMetrics:
    run_id: str
    technology: str = "WASM"
    migration_method: str = "WASM-migration"
    network_migration: str = "no"
    checkpoint_ms: int = 0
    checkpoint_us: int = 0
    checkpoint_ns: int = 0
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
    predump_ms: int = 0
    final_dump_ms: int = 0
    total_ms: int = 0
    lazy_pages_active_ms: int = 0
    lazy_pages_log_bytes: int = 0
    archive_create_ms: int = 0
    transfer_setup_ms: int = 0
    transfer_send_ms: int = 0
    transfer_receive_ms: int = 0
    transfer_cleanup_ms: int = 0
    unpack_ms: int = 0


FIELDNAMES = [
    "run_id",
    "technology",
    "migration_method",
    "network_migration",
    "checkpoint_ms",
    "checkpoint_us",
    "checkpoint_ns",
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
    "archive_create_ms",
    "transfer_setup_ms",
    "transfer_send_ms",
    "transfer_receive_ms",
    "transfer_cleanup_ms",
    "unpack_ms",
]


def default_csv_path() -> Path:
    return Path(__file__).resolve().parents[2] / "metrics" / "migration_metrics.csv"


def write_metrics(metrics: MigrationMetrics, csv_path: Path | None = None) -> None:
    path = csv_path or default_csv_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames and reader.fieldnames != FIELDNAMES:
                rows = list(reader)
                with path.open("w", newline="", encoding="utf-8") as out:
                    writer = csv.DictWriter(out, fieldnames=FIELDNAMES)
                    writer.writeheader()
                    for old_row in rows:
                        writer.writerow(
                            {field: old_row.get(field, "") for field in FIELDNAMES}
                        )

    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            writer.writeheader()
        row = asdict(metrics)
        row["bandwidth_mbps"] = f"{metrics.bandwidth_mbps:.2f}"
        writer.writerow({field: row.get(field, "") for field in FIELDNAMES})
