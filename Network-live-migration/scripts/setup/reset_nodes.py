#!/usr/bin/env python3
"""
Reset nodes to a clean state before running CRIU migration tests.

Cleans up:
- Old TCP workload processes
- CRIU dump directories
- Log files
- State files

This script only performs cleanup; start your application separately after it finishes.
"""

import subprocess
import sys
import time
from datetime import datetime


def log(msg: str):
    """Print timestamped log message."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def exec_cmd(node: str, cmd: str) -> tuple[int, str, str]:
    """Execute command on a node and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["multipass", "exec", node, "--", "bash", "-c", cmd],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _kill_tcp_processes(node: str) -> None:
    """Best-effort kill of legacy TCP workload processes on a node."""
    patterns = [
        "tcp-howto",
        "tcp_client.py",
        "tcp_echo.py",
        "tcp_server.py",
    ]
    for pattern in patterns:
        exec_cmd(node, f"sudo pkill -9 -f '{pattern}' 2>/dev/null || true")


def _remove_vip_alias(node: str, vip: str) -> None:
    """Remove VIP from any interface on node (best-effort)."""
    exec_cmd(
        node,
        (
            "for iface in $(ip -o -4 addr show | "
            f"awk -v vip='{vip}' '$4 ~ \"^\"vip\"/\" {{print $2}}'); do "
            f'sudo ip addr del {vip}/32 dev "$iface" 2>/dev/null || '
            f'sudo ip addr del {vip}/24 dev "$iface" 2>/dev/null || true; '
            "done"
        ),
    )
    exec_cmd(node, f"sudo ip neigh del {vip} dev ens3 2>/dev/null || true")


def reset_nodes(
    source: str,
    dest: str,
    server: str = "edge-host-1",
    vip: str = "10.22.132.250",
) -> bool:
    """Reset all TCP migration nodes to a clean state (cleanup only, no app start)."""

    log(f"Resetting nodes: {source} -> {dest} (server: {server}, vip: {vip})")

    # Step 1: Kill TCP processes on source (best-effort)
    log("Step 1: Killing any existing processes on source...")

    # Kill by PID files used by the TCP workload
    for pidfile in ["/home/ubuntu/tcp_client.pid", "/home/ubuntu/client.pid"]:
        _, old_pid, _ = exec_cmd(source, f"cat {pidfile} 2>/dev/null || echo ''")
        if old_pid and old_pid.isdigit():
            log(f"  Killing PID {old_pid} (from {pidfile})")
            exec_cmd(source, f"kill -9 {old_pid} 2>/dev/null || true")
            time.sleep(0.2)

    # Kill by process name (in case PIDs don't match)
    log("  Killing any remaining background processes...")
    _kill_tcp_processes(source)
    # CRIU post-copy can leave a `criu dump --lazy-pages ...` process running as the page-server.
    exec_cmd(
        source,
        "sudo pkill -9 -f '^criu (dump|restore|page-server|lazy-pages)' 2>/dev/null || true",
    )
    exec_cmd(source, "sudo fuser -k 9999/tcp 2>/dev/null || true")
    time.sleep(0.5)

    # Step 2: Clean source node
    log("Step 2: Cleaning source node...")
    exec_cmd(source, "rm -f /home/ubuntu/tcp_client.pid /home/ubuntu/client.pid")
    exec_cmd(
        source,
        "rm -f /home/ubuntu/tcp_client.out /home/ubuntu/tcp_vip.txt /home/ubuntu/tcp_server_endpoint.txt",
    )
    exec_cmd(
        source,
        "rm -f /home/ubuntu/tcp-howto.c /home/ubuntu/CRIU-tcp-client.tar.gz 2>/dev/null || true",
    )
    exec_cmd(source, "sudo rm -rf /tmp/CRIU-tcp-client* /tmp/criu* 2>/dev/null || true")
    _remove_vip_alias(source, vip)

    # Step 3: Clean destination node
    log("Step 3: Cleaning destination node...")

    # Kill by PID files on destination
    for pidfile in ["/home/ubuntu/tcp_client.pid", "/home/ubuntu/client.pid"]:
        _, old_pid, _ = exec_cmd(dest, f"cat {pidfile} 2>/dev/null || echo ''")
        if old_pid and old_pid.isdigit():
            log(f"  Killing PID {old_pid} on destination (from {pidfile})")
            exec_cmd(dest, f"kill -9 {old_pid} 2>/dev/null || true")
            time.sleep(0.2)

    # Kill all remaining processes by name on destination
    log("  Killing all remaining TCP processes on destination...")
    _kill_tcp_processes(dest)
    exec_cmd(
        dest,
        "sudo pkill -9 -f '^criu (dump|restore|page-server|lazy-pages)' 2>/dev/null || true",
    )
    exec_cmd(dest, "sudo fuser -k 9999/tcp 2>/dev/null || true")
    time.sleep(0.5)

    # Clean files on destination (separate commands for robustness)
    exec_cmd(dest, "rm -f /home/ubuntu/tcp_client.pid /home/ubuntu/client.pid")
    exec_cmd(
        dest,
        "rm -f /home/ubuntu/tcp_client.out /home/ubuntu/tcp_vip.txt /home/ubuntu/tcp_server_endpoint.txt",
    )
    exec_cmd(
        dest,
        "rm -f /home/ubuntu/tcp-howto.c /home/ubuntu/CRIU-tcp-client.tar.gz 2>/dev/null || true",
    )
    exec_cmd(dest, "sudo rm -rf /tmp/CRIU-tcp-client* /tmp/criu* 2>/dev/null || true")
    _remove_vip_alias(dest, vip)

    # Step 4: Clean server / relay node as well
    if server:
        log(f"Step 4: Cleaning server node ({server})...")
        for pidfile in ["/home/ubuntu/tcp_server.pid"]:
            _, old_pid, _ = exec_cmd(server, f"cat {pidfile} 2>/dev/null || echo ''")
            if old_pid and old_pid.isdigit():
                log(f"  Killing PID {old_pid} on server (from {pidfile})")
                exec_cmd(server, f"kill -9 {old_pid} 2>/dev/null || true")
                time.sleep(0.2)

        _kill_tcp_processes(server)
        exec_cmd(
            server,
            "sudo pkill -9 -f '^criu (dump|restore|page-server|lazy-pages)' 2>/dev/null || true",
        )
        exec_cmd(server, "sudo fuser -k 5000/tcp 2>/dev/null || true")
        exec_cmd(server, "sudo fuser -k 25565/tcp 2>/dev/null || true")
        exec_cmd(
            server,
            "rm -f /home/ubuntu/tcp_server.pid /home/ubuntu/tcp_server.out /home/ubuntu/tcp_echo.py /home/ubuntu/tcp_client.py",
        )
        exec_cmd(
            server,
            "sudo rm -rf /tmp/CRIU-tcp-server* /tmp/CRIU-tcp-client* /tmp/criu* 2>/dev/null || true",
        )
        _remove_vip_alias(server, vip)

    log("✓ Reset complete, nodes are clean")
    log("  Next: Start your application")
    return True


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <source_node> <dest_node>")
        print(f"Example: {sys.argv[0]} edge-node-1 edge-node-2")
        sys.exit(1)

    source = sys.argv[1]
    dest = sys.argv[2]
    server = sys.argv[3] if len(sys.argv) > 3 else "edge-host-1"
    vip = sys.argv[4] if len(sys.argv) > 4 else "10.22.132.250"

    success = reset_nodes(source, dest, server, vip)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
