#!/usr/bin/env python3
"""
analyze_results.py — Statistical analysis of migration experiment results.

Loads experiment result JSON/CSV files from the results directory, computes
descriptive statistics (mean, median, std-dev, confidence intervals), and
prints a comparison table suitable for research papers.

Usage:
    python3 analyze_results.py --results-dir ../results
    python3 analyze_results.py --file ../results/experiment_20260314_120000.json
    python3 analyze_results.py --results-dir ../results --output analysis_report.json
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict
from typing import Optional


# --------------------------------------------------------------------------- #
# Statistical helpers
# --------------------------------------------------------------------------- #

def mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def variance(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = mean(vals)
    return sum((v - m) ** 2 for v in vals) / (len(vals) - 1)


def std_dev(vals: list[float]) -> float:
    return math.sqrt(variance(vals))


def median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def percentile(vals: list[float], p: float) -> float:
    """Compute p-th percentile (0–100) using linear interpolation."""
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def confidence_interval_95(vals: list[float]) -> tuple[float, float]:
    """95 % confidence interval using t-distribution approximation."""
    n = len(vals)
    if n < 2:
        return (mean(vals), mean(vals))
    m = mean(vals)
    se = std_dev(vals) / math.sqrt(n)
    # t critical value for 95% CI — use 1.96 for large n, 2.262 for n=10
    t_crit = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
               6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}.get(n - 1, 1.96)
    margin = t_crit * se
    return (round(m - margin, 2), round(m + margin, 2))


def describe(vals: list[float]) -> dict:
    if not vals:
        return {}
    ci = confidence_interval_95(vals)
    return {
        "n": len(vals),
        "mean": round(mean(vals), 2),
        "median": round(median(vals), 2),
        "std_dev": round(std_dev(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "p25": round(percentile(vals, 25), 2),
        "p75": round(percentile(vals, 75), 2),
        "p95": round(percentile(vals, 95), 2),
        "ci95_low": ci[0],
        "ci95_high": ci[1],
    }


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #

def load_json_results(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


def load_csv_results(path: str) -> list[dict]:
    results = []
    with open(path) as f:
        for row in csv.DictReader(f):
            results.append({
                "migration_type": row.get("strategy", ""),
                "iteration": int(row.get("iteration", 0)),
                "timings_ms": {
                    "total_downtime": float(row["total_downtime_ms"]) if row.get("total_downtime_ms") else None,
                    "total_migration": float(row["total_migration_ms"]) if row.get("total_migration_ms") else None,
                },
                "data_transferred_mb": float(row["data_transferred_mb"]) if row.get("data_transferred_mb") else None,
            })
    return results


def load_all_results(results_dir: str) -> list[dict]:
    all_results: list[dict] = []
    for pattern in ["experiment_*.json", "*.json"]:
        for path in sorted(glob.glob(os.path.join(results_dir, pattern))):
            try:
                all_results.extend(load_json_results(path))
            except (json.JSONDecodeError, OSError):
                pass
    for path in sorted(glob.glob(os.path.join(results_dir, "summary_*.csv"))):
        try:
            all_results.extend(load_csv_results(path))
        except (KeyError, ValueError, OSError):
            pass
    return all_results


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #

def group_by_strategy(results: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        strategy = r.get("migration_type") or r.get("strategy") or "unknown"
        groups[strategy].append(r)
    return dict(groups)


def extract_metric(results: list[dict], key: str) -> list[float]:
    vals = []
    for r in results:
        v = None
        if "." in key:
            # Nested key like "timings_ms.total_downtime"
            parts = key.split(".")
            obj = r
            for p in parts:
                obj = obj.get(p, {}) if isinstance(obj, dict) else None
            v = obj
        else:
            v = r.get(key)
        if v is not None and not isinstance(v, dict):
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
    return vals


def analyse(groups: dict[str, list[dict]]) -> dict:
    metrics = {
        "total_downtime_ms":   "timings_ms.total_downtime",
        "total_migration_ms":  "timings_ms.total_migration",
        "data_transferred_mb": "data_transferred_mb",
    }
    analysis: dict[str, dict] = {}
    for strategy, results in groups.items():
        analysis[strategy] = {}
        for label, key in metrics.items():
            vals = extract_metric(results, key)
            analysis[strategy][label] = describe(vals)
    return analysis


def print_table(analysis: dict) -> None:
    strategies = sorted(analysis.keys())
    metrics = ["total_downtime_ms", "total_migration_ms", "data_transferred_mb"]

    col_w = 18
    hdr = f"{'Strategy':<16}" + "".join(f"{'Metric':<{col_w}}" for _ in metrics)

    print("\n" + "=" * 80)
    print("MIGRATION BENCHMARK — STATISTICAL SUMMARY")
    print("=" * 80)

    for metric in metrics:
        print(f"\n  {metric.upper().replace('_', ' ')}")
        print(f"  {'Strategy':<16} {'Mean':>10} {'Median':>10} {'Std':>8} {'Min':>8} {'Max':>8} {'CI95':>18}")
        print(f"  {'-'*16} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*18}")
        for strategy in strategies:
            stats = analysis.get(strategy, {}).get(metric, {})
            if not stats:
                continue
            ci = f"[{stats['ci95_low']}, {stats['ci95_high']}]"
            unit = "ms" if "ms" in metric else "MB"
            print(f"  {strategy:<16} {stats['mean']:>9.1f}{unit[0]} "
                  f"{stats['median']:>9.1f}{unit[0]} "
                  f"{stats['std_dev']:>7.1f}{unit[0]} "
                  f"{stats['min']:>7.1f}{unit[0]} "
                  f"{stats['max']:>7.1f}{unit[0]} "
                  f"{ci:>18}")

    print("\n" + "=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse migration experiment results")
    parser.add_argument("--results-dir", default="../results", help="Results directory")
    parser.add_argument("--file", default=None, help="Load specific JSON result file")
    parser.add_argument("--output", default=None, help="Save analysis to JSON file")
    args = parser.parse_args()

    if args.file:
        results = load_json_results(args.file)
    else:
        results = load_all_results(args.results_dir)

    if not results:
        print("No results found. Run experiments first.", file=sys.stderr)
        sys.exit(1)

    print(f"[analysis] Loaded {len(results)} result(s)")
    groups = group_by_strategy(results)
    print(f"[analysis] Strategies found: {', '.join(sorted(groups.keys()))}")
    print(f"[analysis] Samples per strategy: "
          + ", ".join(f"{k}={len(v)}" for k, v in sorted(groups.items())))

    analysis = analyse(groups)
    print_table(analysis)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            json.dump({"groups": {k: len(v) for k, v in groups.items()},
                       "analysis": analysis}, f, indent=2)
        print(f"[analysis] Saved to {args.output}")


if __name__ == "__main__":
    main()
