"""Run every migration benchmark suite under each configured network profile.

This is the top-level experiment driver:
1. read `network_profiles.json`;
2. apply tc/netem shaping between Multipass VMs;
3. run each command from `benchmarks.json`;
4. keep one run_all session log plus one log per benchmark command;
5. optionally override host/direct repeat counts for all suites;
6. optionally defer plot generation until all suites finish;
7. always remove traffic-control rules at the end.
"""

import json
import subprocess
import time
import sys
import argparse
import csv
import datetime as _dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import shlex
from typing import Callable

# Configuration
CONFIG_FILE = "network_profiles.json"
REPO_ROOT = Path(__file__).resolve().parent
NODES = ["edge-node-1", "edge-node-2", "edge-host-1"]
COOLDOWN_SECONDS = 60  # Time to let the system rest and flush buffers between runs
BENCHMARKS_FILE = "benchmarks.json"

_LOG_FILE = None


@dataclass
class PlotConfig:
    csv_path: Path
    run_logs_dir: Path
    plots_dir: Path
    node_metrics_dir: Path
    script_path: Path


@dataclass
class DeferredPlotJob:
    profile_name: str
    benchmark_name: str
    run_ids_files: list[Path]
    out_dir: Path
    config: PlotConfig


def log(msg=""):
    print(msg)
    if _LOG_FILE is not None:
        _LOG_FILE.write(f"{msg}\n")
        _LOG_FILE.flush()


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "unnamed"


@lru_cache(maxsize=None)
def _cmd_supports_flag(cmd: str, flag: str) -> bool:
    """
    Best-effort detection for whether a `python3 some_script.py ...` command supports a flag.
    Avoids injecting flags into commands that would fail with "unrecognized arguments".
    """
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False

    if not parts or parts[0] not in ("python", "python3"):
        return False

    script = None
    for token in parts[1:5]:
        if token.endswith(".py"):
            script = token
            break
    if script is None:
        return False

    script_path = Path(script)
    if not script_path.exists() or not script_path.is_file():
        return False
    try:
        return flag in script_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def load_benchmarks() -> list[dict]:
    """Load and validate `benchmarks.json` before any long experiment starts."""
    try:
        with open(BENCHMARKS_FILE, "r", encoding="utf-8") as f:
            benchmarks = json.load(f)
    except FileNotFoundError:
        log(f"[ERROR] {BENCHMARKS_FILE} not found.")
        return []

    if not isinstance(benchmarks, list) or not benchmarks:
        log(f"[ERROR] {BENCHMARKS_FILE} must be a non-empty JSON list.")
        return []

    valid: list[dict] = []
    for i, bench in enumerate(benchmarks, start=1):
        if not isinstance(bench, dict):
            log(f"[ERROR] Benchmark #{i} must be an object.")
            return []
        name = bench.get("name")
        command = bench.get("command")
        if not isinstance(name, str) or not name.strip():
            log(f"[ERROR] Benchmark #{i} has missing/invalid name.")
            return []
        if not isinstance(command, str) or not command.strip():
            log(f"[ERROR] Benchmark '{name}' has missing/invalid command.")
            return []
        if "{profile_name}" not in command:
            log(
                f"[WARN] Benchmark '{name}' does not receive profile_name; "
                "metrics may be harder to group by network profile."
            )
        valid.append({"name": name, "command": command})
    return valid


def _maybe_inject_continue_on_failure(cmd: str, enabled: bool) -> str:
    if not enabled:
        return cmd
    if "--continue-on-failure" in cmd:
        return cmd
    if not _cmd_supports_flag(cmd, "--continue-on-failure"):
        return cmd
    return f"{cmd} --continue-on-failure"


def _inject_no_plots_for_deferred_generation(cmd: str, enabled: bool) -> str:
    if not enabled:
        return cmd
    if "--no-plots" in cmd:
        return cmd
    if not _cmd_supports_flag(cmd, "--no-plots"):
        return cmd
    return f"{cmd} --no-plots"


def _replace_or_append_flag(tokens: list[str], flag: str, value: str) -> bool:
    for i, token in enumerate(tokens):
        if token == flag:
            if i + 1 < len(tokens):
                tokens[i + 1] = value
            else:
                tokens.append(value)
            return True
        if token.startswith(f"{flag}="):
            tokens[i] = f"{flag}={value}"
            return True
    return False


def _override_suite_run_counts(cmd: str, runs: int | None) -> str:
    if runs is None:
        return cmd
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return cmd

    runs_value = str(runs)
    for flag in ("--host-runs", "--direct-runs"):
        if not _cmd_supports_flag(cmd, flag):
            continue
        if not _replace_or_append_flag(tokens, flag, runs_value):
            tokens.extend([flag, runs_value])
    return shlex.join(tokens)


def _plot_config_for_command(cmd: str) -> PlotConfig | None:
    """Return plot paths for known benchmark suite commands."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    joined = " ".join(tokens)

    if "Container/scripts/orchestrators/repeat_benchmarks.py" in joined:
        base = REPO_ROOT / "Container"
    elif "Game-of-life-migration/scripts/orchestrators/repeat_benchmarks.py" in joined:
        base = REPO_ROOT / "Game-of-life-migration"
    elif (
        "Network-live-migration/scripts/orchestrators/repeat_tcp_client_benchmarks.py"
        in joined
    ):
        base = REPO_ROOT / "Network-live-migration"
    elif "WASM-migration/scripts/orchestrators/repeat_wasm_benchmarks.py" in joined:
        base = REPO_ROOT / "WASM-migration"
    else:
        return None

    return PlotConfig(
        csv_path=base / "metrics" / "migration_metrics.csv",
        run_logs_dir=base / "metrics" / "run_logs",
        plots_dir=base / "metrics" / "plots",
        node_metrics_dir=base / "metrics" / "node_exporter",
        script_path=base / "scripts" / "visualization" / "generate_all_plots.py",
    )


def _list_run_id_files(config: PlotConfig | None) -> set[Path]:
    if config is None or not config.run_logs_dir.exists():
        return set()
    return set(config.run_logs_dir.glob("*.run_ids.txt"))


def _new_run_id_file(before: set[Path], config: PlotConfig | None) -> Path | None:
    if config is None:
        return None
    after = _list_run_id_files(config)
    new_files = [path for path in after - before if path.exists()]
    if not new_files:
        return None
    new_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return new_files[0]


def _run_ids_file_has_content(path: Path | None) -> bool:
    if path is None or not path.exists():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except Exception:
        return False


def _run_chunks(
    runs: int | None, chunk_size: int | None
) -> list[tuple[int, int, int | None]]:
    if runs is None or chunk_size is None or chunk_size >= runs:
        return [(1, 1, runs)]

    chunks: list[tuple[int, int, int | None]] = []
    remaining = runs
    while remaining > 0:
        chunks.append((len(chunks) + 1, 0, min(chunk_size, remaining)))
        remaining -= chunk_size

    total = len(chunks)
    return [(idx, total, chunk_runs) for idx, _, chunk_runs in chunks]


def _add_deferred_plot_job(
    jobs: list[DeferredPlotJob],
    *,
    profile_name: str,
    benchmark_name: str,
    run_ids_file: Path,
    out_dir: Path,
    config: PlotConfig,
) -> None:
    for job in jobs:
        if (
            job.profile_name == profile_name
            and job.benchmark_name == benchmark_name
            and job.out_dir == out_dir
            and job.config == config
        ):
            if run_ids_file not in job.run_ids_files:
                job.run_ids_files.append(run_ids_file)
            return

    jobs.append(
        DeferredPlotJob(
            profile_name=profile_name,
            benchmark_name=benchmark_name,
            run_ids_files=[run_ids_file],
            out_dir=out_dir,
            config=config,
        )
    )


def _combined_run_ids_file(job: DeferredPlotJob) -> Path:
    if len(job.run_ids_files) == 1:
        return job.run_ids_files[0]

    combined = job.out_dir / "combined.run_ids.txt"
    seen: set[str] = set()
    run_ids: list[str] = []
    for path in job.run_ids_files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            run_id = line.strip()
            if run_id and run_id not in seen:
                seen.add(run_id)
                run_ids.append(run_id)

    combined.write_text(
        "\n".join(run_ids) + ("\n" if run_ids else ""), encoding="utf-8"
    )
    return combined


def run_deferred_plot_jobs(jobs: list[DeferredPlotJob]) -> int:
    """Generate plots after all benchmark suites finish."""
    if not jobs:
        return 0

    rc = 0
    log(f"\n[PLOTS] Generating {len(jobs)} deferred plot set(s)...")
    for job in jobs:
        job.out_dir.mkdir(parents=True, exist_ok=True)
        run_ids_file = _combined_run_ids_file(job)
        cmd = [
            "python3",
            str(job.config.script_path),
            "--csv",
            str(job.config.csv_path),
            "--run-ids-file",
            str(run_ids_file),
            "--out-dir",
            str(job.out_dir),
            "--node-metrics-dir",
            str(job.config.node_metrics_dir),
            "--profile-name",
            job.profile_name,
        ]
        log(
            f"[PLOTS] {job.benchmark_name} | Profile={job.profile_name}\n"
            f"        run_ids={run_ids_file}\n"
            f"        out_dir={job.out_dir}"
        )
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.stdout:
            log(result.stdout.rstrip())
        if result.returncode != 0:
            rc = result.returncode if rc == 0 else rc
            log(
                f"[PLOTS] ERROR: plot generation failed for {job.benchmark_name} "
                f"({job.profile_name}) with code {result.returncode}\n{result.stderr}"
            )
    return rc


def run_cmd(cmd, check=True):
    """Executes a shell command and prints the output."""
    log(f"[CMD] {cmd}")
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if check and result.returncode != 0:
        log(f"[ERROR] Command failed with code {result.returncode}:\n{result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def get_primary_interface(node_name):
    """Dynamically finds the default network interface of a Multipass VM."""
    cmd = (
        f"multipass exec {node_name} -- bash -lc "
        "\"ip -o -4 route show default 2>/dev/null | awk '{print \\$5; exit}'\""
    )
    iface = run_cmd(cmd).strip()
    if iface:
        return iface

    # Fallback: first non-loopback interface (better than passing an empty dev)
    cmd = f"multipass exec {node_name} -- bash -lc \"ls /sys/class/net | grep -v '^lo$' | head -n 1\""
    iface = run_cmd(cmd).strip()
    if not iface:
        log(f"[ERROR] Could not determine primary interface for {node_name}")
        sys.exit(1)
    return iface


def get_node_ip(node_name):
    """Returns the primary IPv4 address of a Multipass VM."""
    cmd = f"multipass exec {node_name} -- hostname -I | awk '{{print $1}}'"
    return run_cmd(cmd)


def clear_tc_rules(node_name, interface):
    """Removes any existing traffic control rules."""
    cmd = f"multipass exec {node_name} -- sudo tc qdisc del dev {interface} root"
    # We do not check return code here, as it fails if no rules exist, which is fine.
    subprocess.run(cmd, shell=True, stderr=subprocess.DEVNULL)


def clear_all_tc_rules(node_name):
    """Best-effort: remove qdisc root from all non-lo interfaces, only valid names."""
    # Only allow interface names with alphanumeric, dash, underscore, or dot (no empty or weird names)
    cmd = (
        f"multipass exec {node_name} -- bash -lc "
        "\"for iface in $(ls /sys/class/net | grep -v '^lo$' | grep -E '^[a-zA-Z0-9._-]+$'); do "
        "sudo tc qdisc del dev $iface root 2>/dev/null || true; "
        'done"'
    )
    subprocess.run(
        cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def restart_multipass_nodes(nodes: list[str]) -> int:
    """Restart benchmark VMs to release leaked/cached memory between chunks."""
    log(f"\n[MULTIPASS] Restarting nodes: {', '.join(nodes)}")
    result = subprocess.run(
        ["multipass", "restart", *nodes],
        text=True,
        capture_output=True,
    )
    if result.stdout:
        log(result.stdout.rstrip())
    if result.stderr:
        log(result.stderr.rstrip())
    if result.returncode != 0:
        log(f"[ERROR] multipass restart failed with code {result.returncode}")
        return result.returncode
    return 0


def discover_nodes() -> tuple[dict[str, str], dict[str, str]]:
    interfaces = {node: get_primary_interface(node) for node in NODES}
    ips = {node: get_node_ip(node) for node in NODES}
    log("\n[INFO] Nodes:")
    for node in NODES:
        log(f"  - {node}: ip={ips[node]} iface={interfaces[node]}")
    return interfaces, ips


def apply_profile_rules(
    profile: dict, interfaces: dict[str, str], ips: dict[str, str]
) -> None:
    bw = profile["bandwidth_mbps"]
    lat = profile["latency_ms"]
    loss = profile["packet_loss_percent"]
    for node in NODES:
        peer_ips = [ips[n] for n in NODES if n != node]
        apply_tc_rules(node, interfaces[node], bw, lat, loss, peer_ips)


def apply_tc_rules(node_name, interface, bw_mbps, latency_ms, loss_pct, peer_ips):
    """
    Applies bandwidth/latency/loss constraints **only to VM-to-VM traffic**.

    This keeps host<->VM control traffic (e.g. multipass exec) and any host-side
    curls/checks from being impacted, while still shaping migration transfers
    and page-server traffic between the VMs.
    """
    clear_tc_rules(node_name, interface)

    if bw_mbps == 1000 and latency_ms <= 1 and loss_pct == 0:
        log(f"[{node_name}] Applying ideal baseline (No TC rules).")
        return

    log(
        f"[{node_name}] Applying TC: {bw_mbps}Mbps, {latency_ms}ms, {loss_pct}% loss on {interface}"
    )

    # HTB root:
    # - default class (1:20): effectively unshaped (host control traffic stays responsive)
    # - shaped class (1:10): bandwidth/latency/loss for VM-to-VM traffic only
    root_rate = "1000mbit"
    run_cmd(
        f"multipass exec {node_name} -- sudo tc qdisc add dev {interface} root handle 1: htb default 20"
    )
    run_cmd(
        f"multipass exec {node_name} -- sudo tc class add dev {interface} parent 1: classid 1:1 htb rate {root_rate}"
    )
    run_cmd(
        f"multipass exec {node_name} -- sudo tc class add dev {interface} parent 1:1 classid 1:20 htb rate {root_rate}"
    )
    run_cmd(
        f"multipass exec {node_name} -- sudo tc class add dev {interface} parent 1:1 classid 1:10 htb rate {bw_mbps}mbit ceil {bw_mbps}mbit"
    )
    run_cmd(
        f"multipass exec {node_name} -- sudo tc qdisc add dev {interface} parent 1:10 handle 10: netem delay {latency_ms}ms loss {loss_pct}%"
    )

    # Classify only traffic to peer VM IPs into the shaped class.
    for ip in peer_ips:
        run_cmd(
            f"multipass exec {node_name} -- sudo tc filter add dev {interface} protocol ip parent 1: prio 1 u32 match ip dst {ip}/32 flowid 1:10"
        )


def execute_benchmarks_with_artifacts(
    *,
    profile_name: str,
    profile_cfg: dict,
    benchmarks: list[dict],
    runs: int | None,
    run_chunk_size: int | None,
    cooldown_seconds: int,
    continue_on_failure: bool,
    defer_suite_plots: bool,
    restart_between_run_chunks: bool,
    restart_and_reapply_profile: Callable[[], int] | None,
    deferred_plot_jobs: list[DeferredPlotJob],
    session_id: str,
    logs_dir: Path,
    timings_writer: csv.DictWriter,
    timings_csv,
):
    """Run benchmarks for one profile, writing per-benchmark logs + timing CSV rows."""
    bw = profile_cfg.get("bandwidth_mbps")
    lat = profile_cfg.get("latency_ms")
    loss = profile_cfg.get("packet_loss_percent")

    rc = 0
    work_units = sum(len(_run_chunks(runs, run_chunk_size)) for _ in benchmarks)
    completed_work_units = 0
    for i, bench in enumerate(benchmarks):
        bench_name = bench.get("name", f"Suite {i+1}")
        raw_cmd = bench.get("command", "")
        chunks = _run_chunks(runs, run_chunk_size)
        for chunk_i, chunk_total, chunk_runs in chunks:
            # Each benchmark command owns its internal repeats. run_all injects
            # profile name, captures stdout/stderr, and records total wall time.
            cmd = raw_cmd.format(profile_name=profile_name)
            cmd = _override_suite_run_counts(cmd, chunk_runs)
            cmd = _maybe_inject_continue_on_failure(cmd, continue_on_failure)
            cmd = _inject_no_plots_for_deferred_generation(cmd, defer_suite_plots)
            plot_config = _plot_config_for_command(cmd)
            run_id_files_before = _list_run_id_files(plot_config)

            chunk_suffix = (
                ""
                if chunk_total == 1
                else f"__chunk-{chunk_i:02d}-of-{chunk_total:02d}"
            )
            bench_log_path = logs_dir / (
                f"{session_id}__{_slugify(profile_name)}__{i+1:02d}__"
                f"{_slugify(bench_name)}{chunk_suffix}.log"
            )

            log(
                f"\n{'='*60}\n[BENCHMARK] Running: {bench_name} | Profile: {profile_name}\n"
                f"[CHUNK] {chunk_i}/{chunk_total} | host/direct runs per suite command: {chunk_runs}\n"
                f"[LOG] {bench_log_path}\n{'='*60}"
            )

            start_wall = _dt.datetime.now().isoformat(timespec="seconds")
            start = time.monotonic()
            exit_code = None
            proc = None
            try:
                with bench_log_path.open("w", encoding="utf-8") as bench_log:
                    bench_log.write(f"[CMD] {cmd}\n\n")
                    bench_log.flush()

                    proc = subprocess.Popen(
                        cmd,
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        bench_log.write(line)
                        bench_log.flush()
                        if _LOG_FILE is not None:
                            _LOG_FILE.write(line)
                            _LOG_FILE.flush()
                    exit_code = proc.wait()
            except KeyboardInterrupt:
                log("\n[WARN] Interrupted during benchmark; terminating subprocess...")
                try:
                    if proc is not None:
                        proc.terminate()
                except Exception:
                    pass
                raise

            end = time.monotonic()
            end_wall = _dt.datetime.now().isoformat(timespec="seconds")
            duration_seconds = round(end - start, 6)

            timings_writer.writerow(
                {
                    "session_id": session_id,
                    "profile_name": profile_name,
                    "bandwidth_mbps": bw,
                    "latency_ms": lat,
                    "packet_loss_percent": loss,
                    "benchmark_index": i + 1,
                    "benchmark_name": (
                        bench_name
                        if chunk_total == 1
                        else f"{bench_name} chunk {chunk_i}/{chunk_total}"
                    ),
                    "command": cmd,
                    "start_time": start_wall,
                    "end_time": end_wall,
                    "duration_seconds": duration_seconds,
                    "exit_code": exit_code,
                    "log_file": str(bench_log_path),
                }
            )
            timings_csv.flush()

            log(
                f"[TIMING] {bench_name} | Profile={profile_name} | "
                f"chunk={chunk_i}/{chunk_total} | {duration_seconds}s | exit={exit_code}"
            )

            if defer_suite_plots and plot_config is not None:
                run_ids_file = _new_run_id_file(run_id_files_before, plot_config)
                if _run_ids_file_has_content(run_ids_file):
                    out_dir = plot_config.plots_dir / (
                        f"{session_id}__{_slugify(profile_name)}__{i+1:02d}__{_slugify(bench_name)}"
                    )
                    _add_deferred_plot_job(
                        deferred_plot_jobs,
                        profile_name=profile_name,
                        benchmark_name=bench_name,
                        run_ids_file=run_ids_file,
                        out_dir=out_dir,
                        config=plot_config,
                    )
                else:
                    log(
                        f"[PLOTS] WARNING: no run_ids file discovered for deferred plots: "
                        f"{bench_name} | Profile={profile_name} | chunk={chunk_i}/{chunk_total}"
                    )

            if exit_code != 0:
                if rc == 0:
                    rc = exit_code
                log(
                    f"[ERROR] Benchmark failed (exit={exit_code}): "
                    f"{bench_name} chunk={chunk_i}/{chunk_total}"
                )
                if not continue_on_failure:
                    return rc

            completed_work_units += 1
            has_more_work = completed_work_units < work_units
            if restart_between_run_chunks and has_more_work:
                if restart_and_reapply_profile is None:
                    log("[ERROR] Cannot restart/reapply profile: callback missing")
                    return rc or 1
                restart_rc = restart_and_reapply_profile()
                if restart_rc != 0:
                    return rc or restart_rc

            if has_more_work:
                log(
                    f"\n[COOLDOWN] Waiting {cooldown_seconds}s before next suite/chunk..."
                )
                time.sleep(cooldown_seconds)

    return rc


def main():
    parser = argparse.ArgumentParser(
        description="Run all benchmark suites across network profiles (tc netem/htb)."
    )
    parser.add_argument(
        "--profiles",
        default="",
        help="Comma-separated profile_name values to run (default: all).",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=int,
        default=COOLDOWN_SECONDS,
        help="Cooldown between suites/profiles (default: 60).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="Override both --host-runs and --direct-runs for every suite command.",
    )
    parser.add_argument(
        "--run-chunk-size",
        type=int,
        default=None,
        help=(
            "Split --runs into multiple suite invocations of this size. "
            "Example: --runs 40 --run-chunk-size 10 runs four h10/d10 chunks."
        ),
    )
    parser.add_argument(
        "--restart-between-run-chunks",
        action="store_true",
        help="Restart Multipass nodes and reapply the active profile between run chunks.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue to the next benchmark/profile even if one fails.",
    )
    parser.add_argument(
        "--out-dir",
        default="run_all_artifacts",
        help="Directory to write run_all logs + CSV timing summary (default: run_all_artifacts).",
    )
    parser.add_argument(
        "--defer-suite-plots",
        action="store_true",
        help="Run suites with --no-plots, then generate profile-specific plots at the end from each suite's run_ids file.",
    )
    args = parser.parse_args()
    if args.runs is not None and args.runs < 1:
        parser.error("--runs must be >= 1")
    if args.run_chunk_size is not None and args.run_chunk_size < 1:
        parser.error("--run-chunk-size must be >= 1")
    if args.run_chunk_size is not None and args.runs is None:
        parser.error("--run-chunk-size requires --runs")
    if args.restart_between_run_chunks and args.run_chunk_size is None:
        parser.error("--restart-between-run-chunks requires --run-chunk-size")
    cooldown_seconds = int(args.cooldown_seconds)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = out_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    session_id = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    session_log_path = out_dir / f"run_all__{session_id}.log"
    timings_csv_path = out_dir / "benchmark_durations.csv"

    global _LOG_FILE
    _LOG_FILE = session_log_path.open("w", encoding="utf-8")

    timings_csv_exists = timings_csv_path.exists()
    timings_csv = timings_csv_path.open(
        "a" if timings_csv_exists else "w", newline="", encoding="utf-8"
    )
    timings_writer = csv.DictWriter(
        timings_csv,
        fieldnames=[
            "session_id",
            "profile_name",
            "bandwidth_mbps",
            "latency_ms",
            "packet_loss_percent",
            "benchmark_index",
            "benchmark_name",
            "command",
            "start_time",
            "end_time",
            "duration_seconds",
            "exit_code",
            "log_file",
        ],
    )
    if not timings_csv_exists:
        timings_writer.writeheader()
    timings_csv.flush()

    log(f"[ARTIFACTS] Session log: {session_log_path}")
    log(f"[ARTIFACTS] Timing CSV : {timings_csv_path}")
    log(f"[ARTIFACTS] Bench logs : {logs_dir}/")

    try:
        benchmarks = load_benchmarks()
        if not benchmarks:
            return 1

        deferred_plot_jobs: list[DeferredPlotJob] = []

        with open(CONFIG_FILE, "r") as f:
            profiles = json.load(f)

        selected = None
        if args.profiles.strip():
            selected = {p.strip() for p in args.profiles.split(",") if p.strip()}

        # Determine interfaces for each node dynamically.
        interfaces, ips = discover_nodes()

        try:
            for profile in profiles:
                p_name = profile["profile_name"]
                if selected and p_name not in selected:
                    continue
                bw = profile["bandwidth_mbps"]
                lat = profile["latency_ms"]
                loss = profile["packet_loss_percent"]

                log(f"\n\n>>>>>>>> INITIALIZING PROFILE: {p_name} <<<<<<<<")

                # Apply network constraints across all nodes.
                apply_profile_rules(profile, interfaces, ips)

                def restart_and_reapply_current_profile() -> int:
                    nonlocal interfaces, ips
                    restart_rc = restart_multipass_nodes(NODES)
                    if restart_rc != 0:
                        return restart_rc
                    interfaces, ips = discover_nodes()
                    apply_profile_rules(profile, interfaces, ips)
                    return 0

                # Optional: Run a pre-hook script here if necessary
                # subprocess.run("bash pre_hook_script.sh", shell=True)

                # Run the experiments (and persist per-benchmark logs + durations)
                rc = execute_benchmarks_with_artifacts(
                    profile_name=p_name,
                    profile_cfg=profile,
                    benchmarks=benchmarks,
                    runs=args.runs,
                    run_chunk_size=args.run_chunk_size,
                    cooldown_seconds=cooldown_seconds,
                    continue_on_failure=args.continue_on_failure,
                    defer_suite_plots=args.defer_suite_plots,
                    restart_between_run_chunks=args.restart_between_run_chunks,
                    restart_and_reapply_profile=restart_and_reapply_current_profile,
                    deferred_plot_jobs=deferred_plot_jobs,
                    session_id=session_id,
                    logs_dir=logs_dir,
                    timings_writer=timings_writer,
                    timings_csv=timings_csv,
                )

                if rc != 0 and not args.continue_on_failure:
                    sys.exit(rc)

                log(
                    f"\n[COOLDOWN] Profile {p_name} complete. Waiting {cooldown_seconds}s before next configuration..."
                )
                time.sleep(cooldown_seconds)

        except KeyboardInterrupt:
            log("\n[WARN] Execution interrupted by user. Cleaning up...")
        finally:
            log("\n[CLEANUP] Removing all TC rules to restore node network states.")
            for node in NODES:
                clear_all_tc_rules(node)
            log("Done.")

        if args.defer_suite_plots:
            plot_rc = run_deferred_plot_jobs(deferred_plot_jobs)
            if plot_rc != 0 and not args.continue_on_failure:
                sys.exit(plot_rc)
    finally:
        try:
            timings_csv.close()
        finally:
            if _LOG_FILE is not None:
                _LOG_FILE.close()
                _LOG_FILE = None


if __name__ == "__main__":
    main()
