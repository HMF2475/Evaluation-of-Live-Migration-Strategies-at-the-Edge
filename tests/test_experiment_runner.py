#!/usr/bin/env python3
"""
Tests for the experiment runner utilities.

Run with:
    python3 -m pytest tests/ -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from experiments.run_experiment import (
    strategy_enabled,
    build_env,
    aggregate_results,
    write_csv_summary,
    STRATEGIES,
    SCRIPT_MAP,
)


SAMPLE_CONFIG = {
    "experiment": {"name": "test", "iterations": 3, "warmup_iterations": 1},
    "nodes": {
        "source": {"host": "src", "ssh_user": "root", "ssh_key": "~/.ssh/id_ed25519", "service_port": 8080},
        "target": {"host": "tgt", "ssh_user": "root", "ssh_key": "~/.ssh/id_ed25519", "service_port": 8080},
    },
    "container": {
        "name": "edge-service",
        "strategies": {
            "cold":      {"enabled": True},
            "pre_copy":  {"enabled": True, "rounds": 3},
            "post_copy": {"enabled": True, "page_server_port": 27000},
            "hybrid":    {"enabled": True, "pre_copy_rounds": 2, "page_server_port": 27001},
        },
    },
    "wasm": {
        "binary": "edge-service.wasm",
        "state_file": "/tmp/wasm_state.json",
        "runtimes": ["wasmtime"],
    },
    "network": {"bandwidth_limit": None, "latency_ms": None, "packet_loss_pct": None},
    "metrics": {"sample_interval_secs": 0.5, "output_dir": "/tmp/test_results"},
}


class TestStrategyEnabled(unittest.TestCase):
    def test_enabled_strategy(self):
        self.assertTrue(strategy_enabled(SAMPLE_CONFIG, "cold"))
        self.assertTrue(strategy_enabled(SAMPLE_CONFIG, "pre_copy"))

    def test_wasm_strategy(self):
        self.assertTrue(strategy_enabled(SAMPLE_CONFIG, "wasm"))

    def test_disabled_strategy(self):
        cfg = json.loads(json.dumps(SAMPLE_CONFIG))
        cfg["container"]["strategies"]["cold"]["enabled"] = False
        self.assertFalse(strategy_enabled(cfg, "cold"))

    def test_wasm_disabled_when_no_runtimes(self):
        cfg = json.loads(json.dumps(SAMPLE_CONFIG))
        cfg["wasm"]["runtimes"] = []
        self.assertFalse(strategy_enabled(cfg, "wasm"))


class TestBuildEnv(unittest.TestCase):
    def test_metrics_dir_in_env(self):
        env = build_env(SAMPLE_CONFIG, "cold")
        self.assertIn("METRICS_DIR", env)
        self.assertEqual(env["METRICS_DIR"], "/tmp/test_results")

    def test_ssh_key_in_env(self):
        env = build_env(SAMPLE_CONFIG, "cold")
        self.assertIn("SSH_KEY", env)

    def test_pre_copy_rounds(self):
        env = build_env(SAMPLE_CONFIG, "pre_copy")
        self.assertEqual(env.get("PRE_COPY_ROUNDS"), "3")

    def test_post_copy_page_server_port(self):
        env = build_env(SAMPLE_CONFIG, "post_copy")
        self.assertEqual(env.get("PAGE_SERVER_PORT"), "27000")

    def test_hybrid_env(self):
        env = build_env(SAMPLE_CONFIG, "hybrid")
        self.assertEqual(env.get("PRE_COPY_ROUNDS"), "2")
        self.assertEqual(env.get("PAGE_SERVER_PORT"), "27001")

    def test_wasm_runtime(self):
        env = build_env(SAMPLE_CONFIG, "wasm")
        self.assertEqual(env.get("WASM_RUNTIME"), "wasmtime")

    def test_service_port(self):
        env = build_env(SAMPLE_CONFIG, "cold")
        self.assertEqual(env.get("SERVICE_PORT"), "8080")


class TestScriptMap(unittest.TestCase):
    def test_all_strategies_mapped(self):
        for s in STRATEGIES:
            self.assertIn(s, SCRIPT_MAP, f"Strategy '{s}' missing from SCRIPT_MAP")

    def test_script_paths_end_with_sh(self):
        for strategy, (script, _) in SCRIPT_MAP.items():
            self.assertTrue(script.endswith(".sh"),
                            f"Script for '{strategy}' should end with .sh: {script}")

    def test_strategy_types(self):
        self.assertEqual(SCRIPT_MAP["wasm"][1], "wasm")
        self.assertEqual(SCRIPT_MAP["cold"][1], "container")
        self.assertEqual(SCRIPT_MAP["pre_copy"][1], "container")


class TestAggregation(unittest.TestCase):
    RESULTS = [
        {"migration_type": "cold", "timings_ms": {"total_downtime": 5000, "total_migration": 12000},
         "data_transferred_mb": 120, "strategy": "cold", "iteration": 1, "timestamp": "20260314"},
        {"migration_type": "wasm", "timings_ms": {"total_downtime": 50, "total_migration": 200},
         "data_transferred_mb": 0.5, "strategy": "wasm", "iteration": 1, "timestamp": "20260314"},
    ]

    def test_aggregate_writes_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            aggregate_results(self.RESULTS, path)
            with open(path) as f:
                data = json.load(f)
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 2)
        finally:
            os.unlink(path)

    def test_csv_summary_columns(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        try:
            write_csv_summary(self.RESULTS, path)
            with open(path) as f:
                header = f.readline().strip()
            expected_cols = {"strategy", "iteration", "total_downtime_ms",
                             "total_migration_ms", "data_transferred_mb", "timestamp"}
            cols = set(header.split(","))
            self.assertEqual(cols, expected_cols)
        finally:
            os.unlink(path)

    def test_csv_summary_rows(self):
        import csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        try:
            write_csv_summary(self.RESULTS, path)
            with open(path) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            strategies = {r["strategy"] for r in rows}
            self.assertIn("cold", strategies)
            self.assertIn("wasm", strategies)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
