#!/usr/bin/env python3
"""
run_experiment.py — Master experiment runner for migration benchmarking.

Orchestrates all migration scenarios (container cold/pre-copy/post-copy/hybrid
and WASM migration) across the configured iterations, collecting metrics for
each run.  Results are saved as individual JSON files and aggregated into a
summary CSV for downstream analysis.

Usage:
    python3 run_experiment.py --config config.yaml
    python3 run_experiment.py --config config.yaml --strategy cold --iterations 3
    python3 run_experiment.py --config config.yaml --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


STRATEGIES = ["cold", "pre_copy", "post_copy", "hybrid", "wasm"]

SCRIPT_MAP = {
    "cold":      ("../container-tests/scripts/cold_migration.sh",     "container"),
    "pre_copy":  ("../container-tests/scripts/precopy_migration.sh",  "container"),
    "post_copy": ("../container-tests/scripts/postcopy_migration.sh", "container"),
    "hybrid":    ("../container-tests/scripts/hybrid_migration.sh",   "container"),
    "wasm":      ("../wasm-tests/scripts/wasm_migrate.sh",            "wasm"),
}


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def strategy_enabled(cfg: dict, strategy: str) -> bool:
    if strategy == "wasm":
        return bool(cfg.get("wasm", {}).get("runtimes"))
    return cfg.get("container", {}).get("strategies", {}).get(strategy, {}).get("enabled", True)


def build_env(cfg: dict, strategy: str) -> dict:
    """Build environment variables for a migration script."""
    env = dict(os.environ)
    nodes = cfg.get("nodes", {})
    src = nodes.get("source", {})
    tgt = nodes.get("target", {})
    container = cfg.get("container", {})
    metrics = cfg.get("metrics", {})
    net = cfg.get("network", {})

    env["METRICS_DIR"] = metrics.get("output_dir", "../results")
    env["SSH_KEY"] = src.get("ssh_key", "~/.ssh/id_ed25519")
    env["SERVICE_PORT"] = str(src.get("service_port", 8080))

    strat_cfg = cfg.get("container", {}).get("strategies", {}).get(strategy, {})

    if strategy == "pre_copy":
        env["PRE_COPY_ROUNDS"] = str(strat_cfg.get("rounds", 3))
    elif strategy == "post_copy":
        env["PAGE_SERVER_PORT"] = str(strat_cfg.get("page_server_port", 27000))
    elif strategy == "hybrid":
        env["PRE_COPY_ROUNDS"] = str(strat_cfg.get("pre_copy_rounds", 2))
        env["PAGE_SERVER_PORT"] = str(strat_cfg.get("page_server_port", 27001))
    elif strategy == "wasm":
        env["WASM_RUNTIME"] = cfg.get("wasm", {}).get("runtimes", ["wasmtime"])[0]
        env["STATE_FILE"] = cfg.get("wasm", {}).get("state_file", "/tmp/wasm_state.json")

    if net.get("bandwidth_limit") or net.get("latency_ms") or net.get("packet_loss_pct"):
        env["TC_IFACE"] = net.get("interface", "eth0")
        if net.get("bandwidth_limit"):
            env["TC_BW"] = str(net["bandwidth_limit"])
        if net.get("latency_ms"):
            env["TC_LATENCY_MS"] = str(net["latency_ms"])
        if net.get("packet_loss_pct"):
            env["TC_LOSS_PCT"] = str(net["packet_loss_pct"])

    return env


def apply_network_conditions(cfg: dict) -> None:
    """Apply tc/netem rules for constrained network simulation."""
    net = cfg.get("network", {})
    iface = net.get("interface", "eth0")
    bw = net.get("bandwidth_limit")
    latency = net.get("latency_ms")
    loss = net.get("packet_loss_pct")

    if not (bw or latency or loss):
        return

    print(f"[tc] Applying network constraints on {iface}…")
    # Clear existing rules
    subprocess.run(["tc", "qdisc", "del", "dev", iface, "root"],
                   capture_output=True)

    args = ["tc", "qdisc", "add", "dev", iface, "root", "netem"]
    if latency:
        args += ["delay", f"{latency}ms"]
    if loss:
        args += ["loss", f"{loss}%"]
    if bw:
        # netem doesn't directly support bandwidth; use tbf for rate limiting
        subprocess.run(["tc", "qdisc", "add", "dev", iface, "root", "handle", "1:",
                        "tbf", "rate", bw, "burst", "256kbit", "latency", "400ms"],
                       check=False)
    else:
        subprocess.run(args, check=False)


def remove_network_conditions(cfg: dict) -> None:
    """Remove tc/netem rules."""
    net = cfg.get("network", {})
    if not (net.get("bandwidth_limit") or net.get("latency_ms") or net.get("packet_loss_pct")):
        return
    iface = net.get("interface", "eth0")
    subprocess.run(["tc", "qdisc", "del", "dev", iface, "root"], capture_output=True)
    print(f"[tc] Removed network constraints from {iface}")


def run_migration(script: str, source: str, target: str, container_or_binary: str,
                  ssh_user: str, env: dict, dry_run: bool) -> Optional[dict]:
    """Execute a migration script and return its result JSON."""
    cmd = ["bash", script, source, target, container_or_binary, ssh_user]
    print(f"[run] {' '.join(cmd)}")

    if dry_run:
        print("[run] DRY RUN — skipping")
        return {"dry_run": True, "script": script}

    result_dir = env.get("METRICS_DIR", "../results")
    before_files = set(Path(result_dir).glob("*.json")) if Path(result_dir).exists() else set()

    t_start = time.monotonic()
    proc = subprocess.run(cmd, env=env, text=True, capture_output=False)
    elapsed = time.monotonic() - t_start

    if proc.returncode != 0:
        print(f"[run] Script exited with code {proc.returncode}")
        return {"error": f"exit_code={proc.returncode}", "elapsed_s": elapsed}

    # Find the newly created result file
    after_files = set(Path(result_dir).glob("*.json")) if Path(result_dir).exists() else set()
    new_files = after_files - before_files
    for f in sorted(new_files):
        with open(f) as fh:
            return json.load(fh)

    return {"elapsed_s": elapsed}


def aggregate_results(all_results: list[dict], output_path: str) -> None:
    """Write aggregated experiment results to JSON."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"[aggregate] Wrote {len(all_results)} results to {output_path}")


def write_csv_summary(all_results: list[dict], output_path: str) -> None:
    """Write a simple CSV summary of key migration metrics."""
    import csv
    fieldnames = [
        "strategy", "iteration", "total_downtime_ms", "total_migration_ms",
        "data_transferred_mb", "timestamp"
    ]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in all_results:
            row = {
                "strategy": r.get("migration_type", r.get("strategy", "unknown")),
                "iteration": r.get("iteration", 0),
                "total_downtime_ms": r.get("timings_ms", {}).get("total_downtime", ""),
                "total_migration_ms": r.get("timings_ms", {}).get("total_migration", ""),
                "data_transferred_mb": r.get("data_transferred_mb", ""),
                "timestamp": r.get("timestamp", ""),
            }
            writer.writerow(row)
    print(f"[csv] Wrote summary CSV to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run migration benchmarking experiments")
    parser.add_argument("--config", default="config.yaml", help="Experiment config YAML")
    parser.add_argument("--strategy", choices=STRATEGIES + ["all"], default="all",
                        help="Which strategy to run (default: all)")
    parser.add_argument("--iterations", type=int, default=None,
                        help="Override number of iterations from config")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing")
    args = parser.parse_args()

    cfg = load_config(args.config)
    exp = cfg.get("experiment", {})

    iterations = args.iterations or exp.get("iterations", 5)
    warmup = exp.get("warmup_iterations", 1)
    output_dir = cfg.get("metrics", {}).get("output_dir", "../results")

    nodes = cfg.get("nodes", {})
    src_host = nodes.get("source", {}).get("host", "localhost")
    tgt_host = nodes.get("target", {}).get("host", "localhost")
    ssh_user = nodes.get("source", {}).get("ssh_user", "root")
    container_name = cfg.get("container", {}).get("name", "edge-service")
    wasm_binary = cfg.get("wasm", {}).get("binary", "edge-service.wasm")

    strategies = [args.strategy] if args.strategy != "all" else STRATEGIES

    all_results: list[dict] = []
    run_ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")

    apply_network_conditions(cfg)

    try:
        for strategy in strategies:
            if not strategy_enabled(cfg, strategy):
                print(f"[config] Strategy '{strategy}' disabled — skipping")
                continue

            script, stype = SCRIPT_MAP[strategy]
            third_arg = wasm_binary if stype == "wasm" else container_name

            print(f"\n{'='*60}")
            print(f"Strategy: {strategy.upper()}  ({iterations} iterations + {warmup} warmup)")
            print(f"{'='*60}")

            env = build_env(cfg, strategy)

            total_iters = warmup + iterations
            for i in range(1, total_iters + 1):
                is_warmup = i <= warmup
                label = f"warmup-{i}" if is_warmup else f"run-{i - warmup}"
                print(f"\n--- {strategy} | {label} ---")

                result = run_migration(script, src_host, tgt_host, third_arg,
                                       ssh_user, env, args.dry_run)

                if result and not is_warmup:
                    result["strategy"] = strategy
                    result["iteration"] = i - warmup
                    result["run_timestamp"] = run_ts
                    all_results.append(result)

                # Brief pause between runs to let the system settle
                if not args.dry_run:
                    time.sleep(5)

    finally:
        remove_network_conditions(cfg)

    if all_results:
        aggregate_path = os.path.join(output_dir, f"experiment_{run_ts}.json")
        csv_path = os.path.join(output_dir, f"summary_{run_ts}.csv")
        aggregate_results(all_results, aggregate_path)
        write_csv_summary(all_results, csv_path)

    print(f"\n[done] Experiment complete. {len(all_results)} result(s) collected.")


if __name__ == "__main__":
    main()
