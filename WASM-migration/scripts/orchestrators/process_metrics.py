"""Small /proc readers for local and Multipass process snapshots."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ProcessSnapshot:
    node: str
    pid: int
    timestamp: float
    exists: bool
    phase: str = ""
    state: str = ""
    vm_rss_bytes: int = 0
    vm_size_bytes: int = 0
    read_bytes: int = 0
    write_bytes: int = 0
    voluntary_ctx_switches: int = 0
    nonvoluntary_ctx_switches: int = 0
    user_ticks: int = 0
    system_ticks: int = 0
    threads: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _kb(value: str) -> int:
    parts = value.split()
    if not parts:
        return 0
    try:
        return int(parts[0]) * 1024
    except ValueError:
        return 0


def _int(value: str) -> int:
    try:
        return int(value.split()[0])
    except (ValueError, IndexError):
        return 0


def _snapshot_from_text(
    node: str, pid: int, status: str, io: str, stat: str, phase: str = ""
) -> ProcessSnapshot:
    snap = ProcessSnapshot(
        node=node, pid=pid, timestamp=time.time(), exists=True, phase=phase
    )
    for line in status.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if key == "State":
            snap.state = value
        elif key == "VmRSS":
            snap.vm_rss_bytes = _kb(value)
        elif key == "VmSize":
            snap.vm_size_bytes = _kb(value)
        elif key == "voluntary_ctxt_switches":
            snap.voluntary_ctx_switches = _int(value)
        elif key == "nonvoluntary_ctxt_switches":
            snap.nonvoluntary_ctx_switches = _int(value)
        elif key == "Threads":
            snap.threads = _int(value)

    for line in io.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key == "read_bytes":
            snap.read_bytes = _int(value.strip())
        elif key == "write_bytes":
            snap.write_bytes = _int(value.strip())

    if stat:
        try:
            after_name = stat.rsplit(")", 1)[1].strip().split()
            snap.user_ticks = int(after_name[11])
            snap.system_ticks = int(after_name[12])
        except (IndexError, ValueError):
            pass
    return snap


def snapshot_local(
    pid: int, node: str = "localhost", phase: str = ""
) -> ProcessSnapshot:
    base = Path("/proc") / str(pid)
    if not base.exists():
        return ProcessSnapshot(
            node=node, pid=pid, timestamp=time.time(), exists=False, phase=phase
        )
    try:
        return _snapshot_from_text(
            node,
            pid,
            (base / "status").read_text(encoding="utf-8", errors="ignore"),
            (base / "io").read_text(encoding="utf-8", errors="ignore"),
            (base / "stat").read_text(encoding="utf-8", errors="ignore"),
            phase,
        )
    except FileNotFoundError:
        return ProcessSnapshot(
            node=node, pid=pid, timestamp=time.time(), exists=False, phase=phase
        )


def snapshot_remote(node: str, pid: int, phase: str = "") -> ProcessSnapshot:
    cmd = (
        f"test -d /proc/{pid} || exit 44; "
        f"printf '__STATUS__\\n'; cat /proc/{pid}/status; "
        f"printf '\\n__IO__\\n'; cat /proc/{pid}/io 2>/dev/null || true; "
        f"printf '\\n__STAT__\\n'; cat /proc/{pid}/stat 2>/dev/null || true"
    )
    result = subprocess.run(
        ["multipass", "exec", node, "--", "bash", "-lc", cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ProcessSnapshot(
            node=node, pid=pid, timestamp=time.time(), exists=False, phase=phase
        )
    text = result.stdout
    try:
        status = text.split("__STATUS__\n", 1)[1].split("\n__IO__\n", 1)[0]
        io = text.split("\n__IO__\n", 1)[1].split("\n__STAT__\n", 1)[0]
        stat = text.split("\n__STAT__\n", 1)[1]
    except IndexError:
        return ProcessSnapshot(
            node=node, pid=pid, timestamp=time.time(), exists=False, phase=phase
        )
    return _snapshot_from_text(node, pid, status, io, stat, phase)


def write_snapshots(path: Path, snapshots: list[ProcessSnapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([snap.to_dict() for snap in snapshots], indent=2, sort_keys=True),
        encoding="utf-8",
    )
