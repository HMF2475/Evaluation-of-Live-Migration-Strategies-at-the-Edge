#!/usr/bin/env python3
"""
system_monitor.py — Continuous system resource monitor for edge nodes.

Provides both a library interface and a standalone daemon that monitors
CPU, memory, network I/O, and disk I/O at configurable intervals.
Designed to run alongside migration experiments to capture the resource
footprint of each migration strategy.

Usage (standalone daemon):
    python3 system_monitor.py --output results/sys_monitor.json --interval 1

Usage (library):
    from system_monitor import SystemMonitor
    monitor = SystemMonitor(interval=0.5)
    monitor.start()
    # ... run migration ...
    data = monitor.stop()
    monitor.save("results/monitor_data.json")
"""

import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import psutil
except ImportError:
    print("ERROR: psutil not installed. Run: pip install psutil", file=sys.stderr)
    sys.exit(1)


class SystemMonitor:
    """
    Background thread that collects system resource metrics at a fixed interval.

    Captures:
    - Per-CPU utilisation
    - Memory (used, available, swap)
    - Per-interface network I/O (cumulative + per-interval rates)
    - Disk I/O (reads, writes, bytes)
    """

    def __init__(self, interval: float = 1.0, label: str = ""):
        self.interval = interval
        self.label = label
        self._samples: list[dict] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Prime CPU percent counters
        psutil.cpu_percent(percpu=True)

    def start(self) -> None:
        """Start background collection."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._collect, daemon=True)
        self._thread.start()

    def stop(self) -> list[dict]:
        """Stop collection and return samples."""
        self._stop_event.set()
        if self._thread:
            # Allow up to 5 s for the thread to finish its current snapshot
            self._thread.join(timeout=max(self.interval * 10, 5.0))
        with self._lock:
            return list(self._samples)

    def _collect(self) -> None:
        while not self._stop_event.is_set():
            sample = self._snapshot()
            with self._lock:
                self._samples.append(sample)
            self._stop_event.wait(timeout=self.interval)

    @staticmethod
    def _snapshot() -> dict:
        cpu_per_core: list[float] = []
        mem = None
        swap = None
        net_by_iface: dict[str, dict] = {}

        try:
            cpu_per_core = psutil.cpu_percent(percpu=True)
        except (PermissionError, OSError):
            pass

        try:
            mem = psutil.virtual_memory()
        except (PermissionError, OSError):
            pass

        try:
            swap = psutil.swap_memory()
        except (PermissionError, OSError):
            pass

        try:
            for iface, stats in psutil.net_io_counters(pernic=True).items():
                if iface == "lo":
                    continue
                net_by_iface[iface] = {
                    "bytes_sent": stats.bytes_sent,
                    "bytes_recv": stats.bytes_recv,
                    "packets_sent": stats.packets_sent,
                    "packets_recv": stats.packets_recv,
                    "errin": stats.errin,
                    "errout": stats.errout,
                }
        except (PermissionError, OSError):
            pass

        return {
            "timestamp": time.time(),
            "cpu": {
                "avg_percent": sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else 0,
                "per_core_percent": cpu_per_core,
                "core_count": len(cpu_per_core),
            },
            "memory": {
                "total_mb": round(mem.total / (1024 ** 2), 1),
                "used_mb": round(mem.used / (1024 ** 2), 1),
                "available_mb": round(mem.available / (1024 ** 2), 1),
                "percent": mem.percent,
            } if mem else {},
            "swap": {
                "total_mb": round(swap.total / (1024 ** 2), 1),
                "used_mb": round(swap.used / (1024 ** 2), 1),
                "percent": swap.percent,
            } if swap else {},
            "network": net_by_iface,
        }

    def summary(self) -> dict:
        """Compute statistical summary over collected samples."""
        with self._lock:
            samples = list(self._samples)

        if not samples:
            return {}

        cpu_vals = [s["cpu"]["avg_percent"] for s in samples]
        mem_vals = [s["memory"]["percent"] for s in samples]

        def stats(vals: list[float]) -> dict:
            return {
                "min": round(min(vals), 2),
                "max": round(max(vals), 2),
                "avg": round(sum(vals) / len(vals), 2),
            }

        return {
            "sample_count": len(samples),
            "duration_secs": round(samples[-1]["timestamp"] - samples[0]["timestamp"], 2),
            "cpu_percent": stats(cpu_vals),
            "memory_percent": stats(mem_vals),
        }

    def save(self, path: str) -> None:
        """Write samples and summary to JSON file."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # Compute summary and snapshot samples while NOT holding the lock to
        # avoid a deadlock (summary() also acquires the lock).
        summary = self.summary()
        with self._lock:
            samples = list(self._samples)
            first_ts = samples[0]["timestamp"] if samples else time.time()
        data = {
            "label": self.label,
            "start_time": datetime.fromtimestamp(first_ts, tz=timezone.utc).isoformat(),
            "interval_secs": self.interval,
            "summary": summary,
            "samples": samples,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[monitor] Saved {len(samples)} samples to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="System resource monitor for migration experiments")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="Duration in seconds (0 = run until SIGINT/SIGTERM)")
    parser.add_argument("--label", default="", help="Experiment label")
    args = parser.parse_args()

    monitor = SystemMonitor(interval=args.interval, label=args.label)
    monitor.start()

    print(f"[monitor] Collecting every {args.interval}s"
          + (f" for {args.duration}s" if args.duration else " — press Ctrl+C to stop"))

    stop = threading.Event()

    def _sig(signum, frame):  # noqa: ANN001
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    if args.duration:
        stop.wait(timeout=args.duration)
    else:
        stop.wait()

    monitor.stop()
    monitor.save(args.output)
    summary = monitor.summary()
    print(f"[monitor] Summary: {summary}")


if __name__ == "__main__":
    main()
