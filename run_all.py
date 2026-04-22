import json
import subprocess
import time
import sys
import argparse
import csv
import datetime as _dt
from pathlib import Path
import re

# Configuration
CONFIG_FILE = "network_profiles.json"
NODES = ["edge-node-1", "edge-node-2", "edge-host-1"]
COOLDOWN_SECONDS = 60  # Time to let the system rest and flush buffers between runs
BENCHMARKS_FILE = "benchmarks.json"

_LOG_FILE = None


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
    cooldown_seconds: int,
    continue_on_failure: bool,
    session_id: str,
    logs_dir: Path,
    timings_writer: csv.DictWriter,
    timings_csv,
):
    """Run benchmarks for one profile, writing per-benchmark logs + timing CSV rows."""
    try:
        with open(BENCHMARKS_FILE, "r") as f:
            benchmarks = json.load(f)
    except FileNotFoundError:
        log(f"[ERROR] {BENCHMARKS_FILE} not found.")
        return 1

    bw = profile_cfg.get("bandwidth_mbps")
    lat = profile_cfg.get("latency_ms")
    loss = profile_cfg.get("packet_loss_percent")

    rc = 0
    for i, bench in enumerate(benchmarks):
        bench_name = bench.get("name", f"Suite {i+1}")
        raw_cmd = bench.get("command", "")
        cmd = raw_cmd.format(profile_name=profile_name)

        bench_log_path = logs_dir / (
            f"{session_id}__{_slugify(profile_name)}__{i+1:02d}__{_slugify(bench_name)}.log"
        )

        log(
            f"\n{'='*60}\n[BENCHMARK] Running: {bench_name} | Profile: {profile_name}\n"
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
                "benchmark_name": bench_name,
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
            f"[TIMING] {bench_name} | Profile={profile_name} | {duration_seconds}s | exit={exit_code}"
        )

        if exit_code != 0:
            if rc == 0:
                rc = exit_code
            log(f"[ERROR] Benchmark failed (exit={exit_code}): {bench_name}")
            if not continue_on_failure:
                break

        if i < len(benchmarks) - 1:
            log(f"\n[COOLDOWN] Waiting {cooldown_seconds}s before next suite...")
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
        "--continue-on-failure",
        action="store_true",
        help="Continue to the next benchmark/profile even if one fails.",
    )
    parser.add_argument(
        "--out-dir",
        default="run_all_artifacts",
        help="Directory to write run_all logs + CSV timing summary (default: run_all_artifacts).",
    )
    args = parser.parse_args()
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
        with open(CONFIG_FILE, "r") as f:
            profiles = json.load(f)

        selected = None
        if args.profiles.strip():
            selected = {p.strip() for p in args.profiles.split(",") if p.strip()}

        # Determine interfaces for each node dynamically
        interfaces = {node: get_primary_interface(node) for node in NODES}
        ips = {node: get_node_ip(node) for node in NODES}
        log("\n[INFO] Nodes:")
        for node in NODES:
            log(f"  - {node}: ip={ips[node]} iface={interfaces[node]}")

        try:
            for profile in profiles:
                p_name = profile["profile_name"]
                if selected and p_name not in selected:
                    continue
                bw = profile["bandwidth_mbps"]
                lat = profile["latency_ms"]
                loss = profile["packet_loss_percent"]

                log(f"\n\n>>>>>>>> INITIALIZING PROFILE: {p_name} <<<<<<<<")

                # Apply network constraints across all nodes
                for node in NODES:
                    peer_ips = [ips[n] for n in NODES if n != node]
                    apply_tc_rules(node, interfaces[node], bw, lat, loss, peer_ips)

                # Optional: Run a pre-hook script here if necessary
                # subprocess.run("bash pre_hook_script.sh", shell=True)

                # Run the experiments (and persist per-benchmark logs + durations)
                rc = execute_benchmarks_with_artifacts(
                    profile_name=p_name,
                    profile_cfg=profile,
                    cooldown_seconds=cooldown_seconds,
                    continue_on_failure=args.continue_on_failure,
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
    finally:
        try:
            timings_csv.close()
        finally:
            if _LOG_FILE is not None:
                _LOG_FILE.close()
                _LOG_FILE = None


if __name__ == "__main__":
    main()
