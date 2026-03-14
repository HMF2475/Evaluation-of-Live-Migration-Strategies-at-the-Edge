#!/usr/bin/env python3
"""
collect_metrics.py — System resource metrics collector for migration experiments.

Samples CPU, memory, network, and disk I/O at a configurable interval and
writes time-series data to a JSON file.  Designed to run in the background
during a migration experiment to capture resource utilisation.

Usage:
    python3 collect_metrics.py --output results/metrics_run1.json --interval 0.5 --duration 60
    python3 collect_metrics.py --pid 1234 --output results/proc_metrics.json
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import psutil
except ImportError:
    print("ERROR: psutil not installed. Run: pip install psutil", file=sys.stderr)
    sys.exit(1)


def sample_system() -> dict:
    """Capture a system-wide resource snapshot."""
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    net = psutil.net_io_counters()
    disk = psutil.disk_io_counters()

    return {
        "timestamp": time.time(),
        "cpu_percent": cpu,
        "memory": {
            "total_mb": mem.total / (1024 ** 2),
            "used_mb": mem.used / (1024 ** 2),
            "available_mb": mem.available / (1024 ** 2),
            "percent": mem.percent,
        },
        "network": {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
        },
        "disk": {
            "read_bytes": disk.read_bytes if disk else 0,
            "write_bytes": disk.write_bytes if disk else 0,
        } if disk else {},
    }


def sample_process(pid: int) -> Optional[dict]:
    """Capture per-process resource snapshot."""
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            cpu = proc.cpu_percent(interval=None)
            mem = proc.memory_info()
            io = proc.io_counters() if hasattr(proc, "io_counters") else None
        return {
            "timestamp": time.time(),
            "pid": pid,
            "cpu_percent": cpu,
            "memory": {
                "rss_mb": mem.rss / (1024 ** 2),
                "vms_mb": mem.vms / (1024 ** 2),
            },
            "io": {
                "read_bytes": io.read_bytes,
                "write_bytes": io.write_bytes,
            } if io else {},
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def compute_deltas(samples: list[dict]) -> list[dict]:
    """Compute per-interval network and disk deltas from cumulative counters."""
    enriched = []
    for i, s in enumerate(samples):
        entry = dict(s)
        if i > 0:
            prev = samples[i - 1]
            dt = s["timestamp"] - prev["timestamp"]
            if dt > 0 and "network" in s and "network" in prev:
                entry["network_delta"] = {
                    "bytes_sent_per_s": (s["network"]["bytes_sent"] - prev["network"]["bytes_sent"]) / dt,
                    "bytes_recv_per_s": (s["network"]["bytes_recv"] - prev["network"]["bytes_recv"]) / dt,
                }
            if dt > 0 and "disk" in s and s["disk"] and "disk" in prev and prev["disk"]:
                entry["disk_delta"] = {
                    "read_bytes_per_s": (s["disk"]["read_bytes"] - prev["disk"]["read_bytes"]) / dt,
                    "write_bytes_per_s": (s["disk"]["write_bytes"] - prev["disk"]["write_bytes"]) / dt,
                }
        enriched.append(entry)
    return enriched


def compute_summary(samples: list[dict]) -> dict:
    """Compute min/max/avg statistics over all samples."""
    if not samples:
        return {}

    cpu_vals = [s["cpu_percent"] for s in samples if "cpu_percent" in s]
    mem_vals = [s["memory"]["percent"] for s in samples if "memory" in s]

    def stats(vals: list[float]) -> dict:
        if not vals:
            return {}
        return {
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "avg": round(sum(vals) / len(vals), 2),
        }

    return {
        "sample_count": len(samples),
        "duration_secs": round(samples[-1]["timestamp"] - samples[0]["timestamp"], 2) if len(samples) > 1 else 0,
        "cpu_percent": stats(cpu_vals),
        "memory_percent": stats(mem_vals),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="System metrics collector for migration benchmarks")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--interval", type=float, default=0.5, help="Sampling interval in seconds (default: 0.5)")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="Collection duration in seconds (0 = run until SIGINT)")
    parser.add_argument("--pid", type=int, default=None, help="Monitor specific process PID")
    parser.add_argument("--label", default="", help="Experiment label to embed in output")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    samples: list[dict] = []
    running = True

    def _stop(signum, frame):  # noqa: ANN001
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # Prime CPU percent counters (first call always returns 0.0)
    psutil.cpu_percent(interval=None)
    if args.pid:
        try:
            psutil.Process(args.pid).cpu_percent(interval=None)
        except psutil.NoSuchProcess:
            print(f"WARNING: PID {args.pid} not found", file=sys.stderr)

    start = time.time()
    print(f"[metrics] Collecting samples every {args.interval}s"
          + (f" for {args.duration}s" if args.duration else " (press Ctrl+C to stop)"))

    while running:
        if args.pid:
            sample = sample_process(args.pid)
            if sample is None:
                print("[metrics] Process ended — stopping collection")
                break
        else:
            sample = sample_system()

        samples.append(sample)

        elapsed = time.time() - start
        if args.duration and elapsed >= args.duration:
            break

        time.sleep(args.interval)

    enriched = compute_deltas(samples)
    summary = compute_summary(samples)

    output = {
        "label": args.label,
        "start_time": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
        "interval_secs": args.interval,
        "pid": args.pid,
        "summary": summary,
        "samples": enriched,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    print(f"[metrics] Wrote {len(samples)} samples to {args.output}")
    print(f"[metrics] Summary: {summary}")


if __name__ == "__main__":
    main()
