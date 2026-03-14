#!/usr/bin/env python3
"""
migration_timer.py — Precise migration timing utility.

Provides a context-manager-based timer and a CLI tool for measuring
the duration of arbitrary shell commands with microsecond precision.

Usage (CLI):
    python3 migration_timer.py --label "cold_transfer" -- rsync -az source/ dest/

Usage (library):
    from migration_timer import MigrationTimer
    with MigrationTimer("transfer") as t:
        subprocess.run(["rsync", ...])
    print(t.elapsed_ms)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TimingEvent:
    """A single named timing event within a migration."""
    label: str
    start_ns: int = field(default_factory=time.monotonic_ns)
    end_ns: Optional[int] = None

    def stop(self) -> "TimingEvent":
        self.end_ns = time.monotonic_ns()
        return self

    @property
    def elapsed_ms(self) -> float:
        if self.end_ns is None:
            return (time.monotonic_ns() - self.start_ns) / 1_000_000
        return (self.end_ns - self.start_ns) / 1_000_000

    @property
    def elapsed_s(self) -> float:
        return self.elapsed_ms / 1000

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "elapsed_s": round(self.elapsed_s, 6),
        }


class MigrationTimer:
    """Context manager for timing a migration phase."""

    def __init__(self, label: str):
        self.label = label
        self._event: Optional[TimingEvent] = None

    def __enter__(self) -> "MigrationTimer":
        self._event = TimingEvent(label=self.label)
        return self

    def __exit__(self, *_) -> None:
        if self._event:
            self._event.stop()

    @property
    def elapsed_ms(self) -> float:
        return self._event.elapsed_ms if self._event else 0.0

    @property
    def elapsed_s(self) -> float:
        return self._event.elapsed_s if self._event else 0.0

    def to_dict(self) -> dict:
        return self._event.to_dict() if self._event else {}


class ExperimentTimer:
    """
    Manages multiple named phases of a migration experiment.

    Example:
        timer = ExperimentTimer("cold_migration")
        timer.start("checkpoint")
        # ... do work ...
        timer.stop("checkpoint")
        timer.start("transfer")
        # ... do work ...
        timer.stop("transfer")
        timer.save("results/timings.json")
    """

    def __init__(self, experiment_name: str):
        self.experiment_name = experiment_name
        self._phases: dict[str, TimingEvent] = {}
        self._order: list[str] = []
        self._wall_start = time.time()

    def start(self, phase: str) -> None:
        self._phases[phase] = TimingEvent(label=phase)
        if phase not in self._order:
            self._order.append(phase)

    def stop(self, phase: str) -> float:
        if phase in self._phases:
            self._phases[phase].stop()
            return self._phases[phase].elapsed_ms
        raise KeyError(f"Phase '{phase}' was never started")

    def elapsed_ms(self, phase: str) -> float:
        if phase in self._phases:
            return self._phases[phase].elapsed_ms
        raise KeyError(f"Unknown phase '{phase}'")

    def total_ms(self) -> float:
        """Sum of all completed phases."""
        return sum(e.elapsed_ms for e in self._phases.values() if e.end_ns is not None)

    def to_dict(self) -> dict:
        phases = {phase: self._phases[phase].to_dict() for phase in self._order}
        return {
            "experiment": self.experiment_name,
            "wall_start": self._wall_start,
            "total_ms": round(self.total_ms(), 3),
            "phases": phases,
        }

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        print(f"[timer] Timing results saved to {path}")

    def print_summary(self) -> None:
        data = self.to_dict()
        print(f"\n=== {data['experiment']} ===")
        for phase, info in data["phases"].items():
            print(f"  {phase:<25} {info['elapsed_ms']:>10.1f} ms")
        print(f"  {'TOTAL':<25} {data['total_ms']:>10.1f} ms")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Time a shell command for migration benchmarking")
    parser.add_argument("--label", required=True, help="Label for this timing event")
    parser.add_argument("--output", default=None, help="Optional JSON output file")
    parser.add_argument("--append", action="store_true", help="Append to existing JSON array")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to time (after --)")
    args = parser.parse_args()

    # Strip leading '--' separator if present
    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        parser.error("Please provide a command to time after --")

    with MigrationTimer(args.label) as timer:
        result = subprocess.run(cmd)

    timing = {
        **timer.to_dict(),
        "exit_code": result.returncode,
        "command": cmd,
    }

    print(f"[{args.label}] {timer.elapsed_ms:.1f} ms (exit {result.returncode})")

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

        if args.append and os.path.exists(args.output):
            with open(args.output, "r") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = [existing]
            existing.append(timing)
            data = existing
        else:
            data = [timing]

        with open(args.output, "w") as f:
            json.dump(data, f, indent=2)

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
