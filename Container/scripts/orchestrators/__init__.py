"""
Migration strategy implementations for CRIU-based live migration.

This package provides modularized implementations of different migration strategies:
- cold_migration: Stop, checkpoint, transfer, restore (baseline)
- precopy_migration: Live with pre-dumps (optimized, with downtime fix)
- postcopy_migration: On-demand paging (lazy-pages)
"""

from .migration_strategy import MigrationStrategy
from .cold_migration import ColdMigration
from .precopy_migration import PrecopyMigration
from .postcopy_migration import PostcopyMigration

__all__ = [
    "MigrationStrategy",
    "ColdMigration",
    "PrecopyMigration",
    "PostcopyMigration",
]
