#!/usr/bin/env python3
"""
Tests for metrics collection and analysis utilities.

Run with:
    python3 -m pytest tests/ -v
    python3 tests/test_metrics.py
"""

import json
import math
import os
import sys
import tempfile
import time
import unittest

# Make parent-level modules importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from metrics.migration_timer import TimingEvent, MigrationTimer, ExperimentTimer
from analysis.analyze_results import (
    mean, median, std_dev, variance, percentile,
    confidence_interval_95, describe,
    group_by_strategy, extract_metric, analyse,
)


# --------------------------------------------------------------------------- #
# migration_timer tests
# --------------------------------------------------------------------------- #

class TestTimingEvent(unittest.TestCase):
    def test_elapsed_ms_positive(self):
        event = TimingEvent(label="test")
        time.sleep(0.01)
        event.stop()
        self.assertGreater(event.elapsed_ms, 0)

    def test_elapsed_ms_before_stop(self):
        event = TimingEvent(label="test")
        self.assertGreater(event.elapsed_ms, 0)

    def test_elapsed_s(self):
        event = TimingEvent(label="test")
        time.sleep(0.01)
        event.stop()
        self.assertAlmostEqual(event.elapsed_ms / 1000, event.elapsed_s, places=6)

    def test_to_dict_keys(self):
        event = TimingEvent(label="phase1")
        event.stop()
        d = event.to_dict()
        self.assertIn("label", d)
        self.assertIn("elapsed_ms", d)
        self.assertIn("elapsed_s", d)
        self.assertEqual(d["label"], "phase1")


class TestMigrationTimer(unittest.TestCase):
    def test_context_manager(self):
        with MigrationTimer("transfer") as t:
            time.sleep(0.01)
        self.assertGreater(t.elapsed_ms, 5)

    def test_elapsed_s(self):
        with MigrationTimer("x") as t:
            time.sleep(0.01)
        self.assertAlmostEqual(t.elapsed_ms / 1000, t.elapsed_s, places=6)

    def test_to_dict(self):
        with MigrationTimer("phase") as t:
            pass
        d = t.to_dict()
        self.assertIn("label", d)
        self.assertEqual(d["label"], "phase")


class TestExperimentTimer(unittest.TestCase):
    def test_phase_timing(self):
        et = ExperimentTimer("test_exp")
        et.start("step1")
        time.sleep(0.01)
        ms = et.stop("step1")
        self.assertGreater(ms, 5)

    def test_unknown_phase_raises(self):
        et = ExperimentTimer("test_exp")
        with self.assertRaises(KeyError):
            et.stop("nonexistent")

    def test_total_ms(self):
        et = ExperimentTimer("test_exp")
        et.start("a")
        time.sleep(0.005)
        et.stop("a")
        et.start("b")
        time.sleep(0.005)
        et.stop("b")
        self.assertGreater(et.total_ms(), 8)

    def test_save_and_load(self):
        et = ExperimentTimer("save_test")
        et.start("transfer")
        time.sleep(0.005)
        et.stop("transfer")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name

        try:
            et.save(path)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["experiment"], "save_test")
            self.assertIn("transfer", data["phases"])
            self.assertGreater(data["total_ms"], 0)
        finally:
            os.unlink(path)

    def test_to_dict_structure(self):
        et = ExperimentTimer("struct_test")
        et.start("p1")
        et.stop("p1")
        d = et.to_dict()
        self.assertIn("experiment", d)
        self.assertIn("total_ms", d)
        self.assertIn("phases", d)
        self.assertIn("p1", d["phases"])


# --------------------------------------------------------------------------- #
# analyze_results tests
# --------------------------------------------------------------------------- #

class TestStatistics(unittest.TestCase):
    def test_mean(self):
        self.assertAlmostEqual(mean([1, 2, 3, 4, 5]), 3.0)

    def test_mean_empty(self):
        self.assertEqual(mean([]), 0.0)

    def test_median_odd(self):
        self.assertEqual(median([1, 3, 5]), 3.0)

    def test_median_even(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_median_empty(self):
        self.assertEqual(median([]), 0.0)

    def test_std_dev(self):
        # Population std = 2.0, sample std (n-1) = sqrt(32/7) ≈ 2.138
        self.assertAlmostEqual(std_dev([2, 4, 4, 4, 5, 5, 7, 9]), 2.138, places=2)

    def test_std_dev_single(self):
        self.assertEqual(std_dev([42.0]), 0.0)

    def test_std_dev_empty(self):
        self.assertEqual(std_dev([]), 0.0)

    def test_percentile_p50(self):
        vals = list(range(1, 101))
        self.assertAlmostEqual(percentile(vals, 50), 50.5, places=0)

    def test_percentile_p95(self):
        vals = list(range(1, 101))
        self.assertGreaterEqual(percentile(vals, 95), 95)

    def test_ci95_contains_mean(self):
        vals = [100, 102, 98, 101, 99]
        low, high = confidence_interval_95(vals)
        self.assertLessEqual(low, mean(vals))
        self.assertGreaterEqual(high, mean(vals))

    def test_ci95_single(self):
        low, high = confidence_interval_95([42.0])
        self.assertEqual(low, 42.0)
        self.assertEqual(high, 42.0)

    def test_describe_keys(self):
        d = describe([10, 20, 30, 40, 50])
        for key in ["n", "mean", "median", "std_dev", "min", "max", "p25", "p75", "p95",
                    "ci95_low", "ci95_high"]:
            self.assertIn(key, d, f"Missing key: {key}")
        self.assertEqual(d["n"], 5)
        self.assertEqual(d["min"], 10)
        self.assertEqual(d["max"], 50)

    def test_describe_empty(self):
        self.assertEqual(describe([]), {})


class TestResultGrouping(unittest.TestCase):
    SAMPLE_RESULTS = [
        {"migration_type": "cold",     "timings_ms": {"total_downtime": 5000, "total_migration": 12000}, "data_transferred_mb": 120},
        {"migration_type": "cold",     "timings_ms": {"total_downtime": 5200, "total_migration": 12500}, "data_transferred_mb": 118},
        {"migration_type": "pre_copy", "timings_ms": {"total_downtime": 800,  "total_migration": 18000}, "data_transferred_mb": 250},
        {"migration_type": "wasm",     "timings_ms": {"total_downtime": 50,   "total_migration": 200},   "data_transferred_mb": 0.5},
    ]

    def test_group_by_strategy(self):
        groups = group_by_strategy(self.SAMPLE_RESULTS)
        self.assertIn("cold", groups)
        self.assertIn("pre_copy", groups)
        self.assertIn("wasm", groups)
        self.assertEqual(len(groups["cold"]), 2)

    def test_extract_metric_flat(self):
        groups = group_by_strategy(self.SAMPLE_RESULTS)
        vals = extract_metric(groups["cold"], "data_transferred_mb")
        self.assertEqual(sorted(vals), [118.0, 120.0])

    def test_extract_metric_nested(self):
        groups = group_by_strategy(self.SAMPLE_RESULTS)
        vals = extract_metric(groups["wasm"], "timings_ms.total_downtime")
        self.assertEqual(vals, [50.0])

    def test_extract_metric_missing(self):
        groups = group_by_strategy(self.SAMPLE_RESULTS)
        vals = extract_metric(groups["cold"], "nonexistent_key")
        self.assertEqual(vals, [])

    def test_analyse_returns_stats(self):
        groups = group_by_strategy(self.SAMPLE_RESULTS)
        result = analyse(groups)
        self.assertIn("cold", result)
        self.assertIn("total_downtime_ms", result["cold"])
        cold_dt = result["cold"]["total_downtime_ms"]
        self.assertEqual(cold_dt["n"], 2)
        self.assertAlmostEqual(cold_dt["mean"], 5100.0)

    def test_analyse_wasm_downtime(self):
        groups = group_by_strategy(self.SAMPLE_RESULTS)
        result = analyse(groups)
        wasm_dt = result["wasm"]["total_downtime_ms"]
        self.assertEqual(wasm_dt["mean"], 50.0)

    def test_wasm_vs_cold_downtime(self):
        """WASM should have significantly lower downtime than cold migration."""
        groups = group_by_strategy(self.SAMPLE_RESULTS)
        result = analyse(groups)
        wasm_mean = result["wasm"]["total_downtime_ms"]["mean"]
        cold_mean = result["cold"]["total_downtime_ms"]["mean"]
        self.assertLess(wasm_mean, cold_mean)

    def test_wasm_vs_cold_data_transferred(self):
        """WASM should transfer significantly less data than cold migration."""
        groups = group_by_strategy(self.SAMPLE_RESULTS)
        result = analyse(groups)
        wasm_data = result["wasm"]["data_transferred_mb"]["mean"]
        cold_data = result["cold"]["data_transferred_mb"]["mean"]
        self.assertLess(wasm_data, cold_data)


# --------------------------------------------------------------------------- #
# system_monitor integration test (no external dependencies beyond psutil)
# --------------------------------------------------------------------------- #

class TestSystemMonitor(unittest.TestCase):
    def test_monitor_collects_samples(self):
        try:
            from metrics.system_monitor import SystemMonitor
        except ImportError:
            self.skipTest("psutil not installed")

        monitor = SystemMonitor(interval=0.1, label="unit-test")
        monitor.start()
        time.sleep(0.35)
        samples = monitor.stop()
        self.assertGreater(len(samples), 1, "Should collect multiple samples")

    def test_monitor_summary_keys(self):
        try:
            from metrics.system_monitor import SystemMonitor
        except ImportError:
            self.skipTest("psutil not installed")

        monitor = SystemMonitor(interval=0.1, label="unit-test")
        monitor.start()
        time.sleep(0.25)
        monitor.stop()
        summary = monitor.summary()
        self.assertIn("sample_count", summary)
        self.assertIn("cpu_percent", summary)
        self.assertIn("memory_percent", summary)

    def test_monitor_save(self):
        try:
            from metrics.system_monitor import SystemMonitor
        except ImportError:
            self.skipTest("psutil not installed")

        monitor = SystemMonitor(interval=0.1)
        monitor.start()
        time.sleep(0.2)
        monitor.stop()

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            monitor.save(path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("samples", data)
            self.assertIn("summary", data)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
