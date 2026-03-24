#!/usr/bin/env python3
"""
Reset nodes to a clean state before running CRIU migration tests.

Cleans up:
- Old counter processes
- CRIU dump directories
- Log files
- State files

Then starts a fresh counter process on the source node.
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

def reset_nodes(source: str, dest: str) -> bool:
    """Reset both nodes to clean state (cleanup only, no app start). Return True on success."""
    
    log(f"Resetting nodes: {source} -> {dest}")
    
    # Step 1: Kill ALL processes on source (any app.pid or counter.pid)
    log("Step 1: Killing any existing processes on source...")
    
    # Kill by PID files (generic app.pid and counter.pid)
    for pidfile in ["/home/ubuntu/app.pid", "/home/ubuntu/counter.pid"]:
        _, old_pid, _ = exec_cmd(source, f"cat {pidfile} 2>/dev/null || echo ''")
        if old_pid and old_pid.isdigit():
            log(f"  Killing PID {old_pid} (from {pidfile})")
            exec_cmd(source, f"kill -9 {old_pid} 2>/dev/null || true")
            time.sleep(0.2)
    
    # Kill by process name (in case PIDs don't match)
    log("  Killing any remaining background processes...")
    exec_cmd(source,
        "ps aux | grep -E 'counter|app\\.py|http\\.server' | grep -v grep | awk '{print $2}' | while read pid; do [ -n \"$pid\" ] && kill -9 \"$pid\" 2>/dev/null || true; done"
    )
    time.sleep(0.5)
    
    # Step 2: Clean source node
    log("Step 2: Cleaning source node...")
    exec_cmd(source, "rm -f /home/ubuntu/app.pid /home/ubuntu/app.log")
    exec_cmd(source, "rm -f /home/ubuntu/counter.pid /home/ubuntu/counter.log")
    exec_cmd(source, "rm -f /home/ubuntu/*counter* /home/ubuntu/CRIU* 2>/dev/null || true")
    exec_cmd(source, "sudo rm -rf /tmp/CRIU-counter* /tmp/criu* 2>/dev/null || true")
    
    # Step 3: Clean destination node
    log("Step 3: Cleaning destination node...")
    
    # Kill by PID files on destination
    for pidfile in ["/home/ubuntu/app.pid", "/home/ubuntu/counter.pid"]:
        _, old_pid, _ = exec_cmd(dest, f"cat {pidfile} 2>/dev/null || echo ''")
        if old_pid and old_pid.isdigit():
            log(f"  Killing PID {old_pid} on destination (from {pidfile})")
            exec_cmd(dest, f"kill -9 {old_pid} 2>/dev/null || true")
            time.sleep(0.2)
    
    # Kill all remaining processes by name on destination
    log("  Killing all remaining counter processes on destination...")
    exec_cmd(dest, "pkill -9 counter || true")
    exec_cmd(dest, "pkill -f 'native-counter' || true")
    exec_cmd(dest, "pkill -f 'counter\\.sh' || true")
    time.sleep(0.5)
    
    # Clean files on destination (separate commands for robustness)
    exec_cmd(dest, "rm -f /home/ubuntu/app.pid /home/ubuntu/app.log")
    exec_cmd(dest, "rm -f /home/ubuntu/counter.pid /home/ubuntu/counter.log")
    exec_cmd(dest, "rm -f /home/ubuntu/*counter* /home/ubuntu/*counter.* 2>/dev/null || true")
    exec_cmd(dest, "rm -f /home/ubuntu/CRIU* 2>/dev/null || true")
    exec_cmd(dest, "sudo rm -rf /tmp/CRIU-counter* /tmp/criu* 2>/dev/null || true")
    
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
    
    success = reset_nodes(source, dest)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
