import json
import subprocess
import time
import sys
import argparse

# Configuration
CONFIG_FILE = "network_profiles.json"
NODES = ["edge-node-1", "edge-node-2", "edge-host-1"]
COOLDOWN_SECONDS = 60  # Time to let the system rest and flush buffers between runs
BENCHMARKS_FILE = "benchmarks.json"


def run_cmd(cmd, check=True):
    """Executes a shell command and prints the output."""
    print(f"[CMD] {cmd}")
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if check and result.returncode != 0:
        print(f"[ERROR] Command failed with code {result.returncode}:\n{result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def get_primary_interface(node_name):
    """Dynamically finds the default network interface of a Multipass VM."""
    cmd = f"multipass exec {node_name} -- ip -o -4 route show to default | awk '{{print $5}}'"
    return run_cmd(cmd)


def get_node_ip(node_name):
    """Returns the primary IPv4 address of a Multipass VM."""
    cmd = f"multipass exec {node_name} -- hostname -I | awk '{{print $1}}'"
    return run_cmd(cmd)


def clear_tc_rules(node_name, interface):
    """Removes any existing traffic control rules."""
    cmd = f"multipass exec {node_name} -- sudo tc qdisc del dev {interface} root"
    # We do not check return code here, as it fails if no rules exist, which is fine.
    subprocess.run(cmd, shell=True, stderr=subprocess.DEVNULL)


def apply_tc_rules(node_name, interface, bw_mbps, latency_ms, loss_pct, peer_ips):
    """
    Applies bandwidth/latency/loss constraints **only to VM-to-VM traffic**.

    This keeps host<->VM control traffic (e.g. multipass exec) and any host-side
    curls/checks from being impacted, while still shaping migration transfers
    and page-server traffic between the VMs.
    """
    clear_tc_rules(node_name, interface)

    if bw_mbps == 1000 and latency_ms <= 1 and loss_pct == 0:
        print(f"[{node_name}] Applying ideal baseline (No TC rules).")
        return

    print(
        f"[{node_name}] Applying TC: {bw_mbps}Mbps, {latency_ms}ms, {loss_pct}% loss on {interface}"
    )

    # Root qdisc: prio with 2 bands
    # - band 1 (1:1): shaped (tbf + netem) for VM-to-VM traffic only
    # - band 2 (1:2): default, unshaped (host control traffic unaffected)
    run_cmd(
        f"multipass exec {node_name} -- sudo tc qdisc add dev {interface} root handle 1: prio bands 2"
    )

    # Band 1: bandwidth limiter + netem
    # Use TBF for rate-limiting and netem for delay/loss.
    # Burst chosen conservatively; latency in TBF just needs to be large enough
    # to queue under the configured delay.
    burst = "32kb"
    tbf_latency = f"{max(1000, int(latency_ms) * 3)}ms"
    run_cmd(
        f"multipass exec {node_name} -- sudo tc qdisc add dev {interface} parent 1:1 handle 10: tbf rate {bw_mbps}mbit burst {burst} latency {tbf_latency}"
    )
    run_cmd(
        f"multipass exec {node_name} -- sudo tc qdisc add dev {interface} parent 10:1 handle 20: netem delay {latency_ms}ms loss {loss_pct}%"
    )

    # Classify only traffic to peer VM IPs into band 1 (shaped)
    for ip in peer_ips:
        run_cmd(
            f"multipass exec {node_name} -- sudo tc filter add dev {interface} protocol ip parent 1: prio 1 u32 match ip dst {ip}/32 flowid 1:1"
        )


def execute_benchmarks(profile_name, cooldown_seconds):
    """Dynamically loads and executes benchmarks from the JSON registry."""
    try:
        with open(BENCHMARKS_FILE, "r") as f:
            benchmarks = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] {BENCHMARKS_FILE} not found.")
        return

    for i, bench in enumerate(benchmarks):
        name = bench.get("name", f"Suite {i+1}")

        # INJECT THE PROFILE NAME HERE
        raw_cmd = bench.get("command")
        cmd = raw_cmd.format(profile_name=profile_name)

        print(
            f"\n{'='*60}\n[BENCHMARK] Running: {name} | Profile: {profile_name}\n{'='*60}"
        )
        result = subprocess.run(cmd, shell=True)
        if result.returncode != 0:
            print(f"[ERROR] Benchmark failed (exit={result.returncode}): {name}")
            return result.returncode

        # Cooldown between different benchmark suites within the same profile
        if i < len(benchmarks) - 1:
            print(f"\n[COOLDOWN] Waiting {cooldown_seconds}s before next suite...")
            time.sleep(cooldown_seconds)
    return 0


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
    args = parser.parse_args()
    cooldown_seconds = int(args.cooldown_seconds)

    with open(CONFIG_FILE, "r") as f:
        profiles = json.load(f)

    selected = None
    if args.profiles.strip():
        selected = {p.strip() for p in args.profiles.split(",") if p.strip()}

    # Determine interfaces for each node dynamically
    interfaces = {node: get_primary_interface(node) for node in NODES}
    ips = {node: get_node_ip(node) for node in NODES}

    try:
        for profile in profiles:
            p_name = profile["profile_name"]
            if selected and p_name not in selected:
                continue
            bw = profile["bandwidth_mbps"]
            lat = profile["latency_ms"]
            loss = profile["packet_loss_percent"]

            print(f"\n\n>>>>>>>> INITIALIZING PROFILE: {p_name} <<<<<<<<")

            # Apply network constraints across all nodes
            for node in NODES:
                peer_ips = [ips[n] for n in NODES if n != node]
                apply_tc_rules(node, interfaces[node], bw, lat, loss, peer_ips)

            # Optional: Run a pre-hook script here if necessary
            # subprocess.run("bash pre_hook_script.sh", shell=True)

            # Run the experiments
            rc = execute_benchmarks(p_name, cooldown_seconds)
            if rc != 0 and not args.continue_on_failure:
                sys.exit(rc)

            print(
                f"\n[COOLDOWN] Profile {p_name} complete. Waiting {cooldown_seconds}s before next configuration..."
            )
            time.sleep(cooldown_seconds)

    except KeyboardInterrupt:
        print("\n[WARN] Execution interrupted by user. Cleaning up...")
    finally:
        print("\n[CLEANUP] Removing all TC rules to restore node network states.")
        for node in NODES:
            clear_tc_rules(node, interfaces[node])
        print("Done.")


if __name__ == "__main__":
    main()
