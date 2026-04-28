#!/usr/bin/env python3
"""Run Edoardo Tinto's WASM migration proof-of-concept on the local host."""

from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WASM_ROOT = SCRIPT_DIR.parent
ORCH_DIR = WASM_ROOT / "scripts" / "orchestrators"
sys.path.insert(0, str(ORCH_DIR))

from metrics import MigrationMetrics, write_metrics  # noqa: E402
from process_metrics import ProcessSnapshot, snapshot_local, write_snapshots  # noqa: E402


PID_RE = re.compile(r"server_pid=(\d+)")
LOG_RE = re.compile(r"^(?P<event>.+) - (?P<sec>\d+) sec - (?P<nsec>\d+) nsec$")
DEFAULT_MODULE = (
    WASM_ROOT / "wasm-migrate-commands" / "wasm_test_computation" / "3mm_with_cr.wasm"
)


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    stdout_target = subprocess.PIPE
    stderr_target = subprocess.PIPE
    stdout_file = None
    stderr_file = None
    try:
        if stdout_path:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_file = stdout_path.open("w", encoding="utf-8")
            stdout_target = stdout_file
        if stderr_path:
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_file = stderr_path.open("w", encoding="utf-8")
            stderr_target = stderr_file
        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=stdout_target,
            stderr=stderr_target,
            text=True,
        )
    finally:
        if stdout_file:
            stdout_file.close()
        if stderr_file:
            stderr_file.close()

    if check and result.returncode != 0:
        stdout = (
            stdout_path.read_text(encoding="utf-8", errors="ignore")
            if stdout_path
            else result.stdout
        )
        stderr = (
            stderr_path.read_text(encoding="utf-8", errors="ignore")
            if stderr_path
            else result.stderr
        )
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return result


def event_times(log_path: Path) -> dict[str, int]:
    events: dict[str, int] = {}
    if not log_path.exists():
        return events
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = LOG_RE.match(line.strip())
        if not match:
            continue
        ns = int(match.group("sec")) * 1_000_000_000 + int(match.group("nsec"))
        events[match.group("event")] = ns
    return events


def elapsed_ms(events: dict[str, int], start: str, end: str) -> int:
    if start not in events or end not in events:
        return 0
    delta_ns = max(0, events[end] - events[start])
    if delta_ns == 0:
        return 0
    return max(1, int((delta_ns + 999_999) // 1_000_000))


def monotonic_elapsed_ms(start_ns: int) -> int:
    delta_ns = max(0, time.monotonic_ns() - start_ns)
    if delta_ns == 0:
        return 0
    return max(1, int((delta_ns + 999_999) // 1_000_000))


def wait_for_log(log_path: Path, needle: str, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if log_path.exists() and needle in log_path.read_text(
            encoding="utf-8", errors="ignore"
        ):
            return
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {needle!r} in {log_path}")


def wait_for_files(paths: list[Path], timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(path.exists() and path.stat().st_size > 0 for path in paths):
            return
        time.sleep(0.05)
    missing = ", ".join(str(path) for path in paths if not path.exists())
    raise TimeoutError(f"timed out waiting for files: {missing}")


def parse_pid(create_stdout: Path) -> int:
    text = create_stdout.read_text(encoding="utf-8", errors="ignore")
    match = PID_RE.search(text)
    if not match:
        raise RuntimeError(
            f"create_command did not print server_pid. See {create_stdout}"
        )
    return int(match.group(1))


def launch_server(
    *,
    commands_dir: Path,
    module_path: Path,
    run_dir: Path,
    label: str,
) -> tuple[int, Path, Path, Path, Path]:
    ipc = run_dir / f"{label}.ipc"
    log = run_dir / f"{label}.log"
    main_memory = run_dir / f"{label}_main_memory.b"
    checkpoint_memory = run_dir / f"{label}_checkpoint_memory.b"
    create_stdout = run_dir / f"{label}_create.out"
    create_stderr = run_dir / f"{label}_create.err"
    ipc.touch()
    log.touch()
    run(
        [
            str(commands_dir / "create_command"),
            str(module_path),
            str(ipc),
            str(main_memory),
            str(checkpoint_memory),
            "-",
            str(log),
        ],
        cwd=commands_dir,
        stdout_path=create_stdout,
        stderr_path=create_stderr,
    )
    pid = parse_pid(create_stdout)
    wait_for_log(log, "request_server - wait for activation")
    return pid, ipc, log, main_memory, checkpoint_memory


def checkpoint(
    *,
    commands_dir: Path,
    ipc: Path,
    log: Path,
    main_memory: Path,
    checkpoint_memory: Path,
    wait_timeout: float,
) -> None:
    run([str(commands_dir / "migrate_command"), str(ipc)], cwd=commands_dir)
    wait_for_log(log, "request_server - checkpoint completed", timeout_s=wait_timeout)
    wait_for_files([main_memory, checkpoint_memory], timeout_s=wait_timeout)


def archive_state(
    archive_path: Path, main_memory: Path, checkpoint_memory: Path
) -> int:
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(main_memory, arcname="main_memory.b")
        tar.add(checkpoint_memory, arcname="checkpoint_memory.b")
    return archive_path.stat().st_size


def restore_archive(archive_path: Path, dest_dir: Path) -> tuple[Path, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(dest_dir)
    return dest_dir / "main_memory.b", dest_dir / "checkpoint_memory.b"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run local host migration using Tinto's existing WASM module and commands."
    )
    parser.add_argument(
        "--run-id", default=f"host-wasm-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    parser.add_argument("--module", type=Path, default=DEFAULT_MODULE)
    parser.add_argument(
        "--commands-dir",
        type=Path,
        default=WASM_ROOT / "wasm-migrate-commands" / "build",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=SCRIPT_DIR / "artifacts" / "host"
    )
    parser.add_argument(
        "--csv", type=Path, default=WASM_ROOT / "metrics" / "migration_metrics.csv"
    )
    parser.add_argument("--warmup-seconds", type=float, default=0.01)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()
    args.module = args.module.resolve()
    args.commands_dir = args.commands_dir.resolve()
    args.out_dir = args.out_dir.resolve()
    args.csv = args.csv.resolve()

    if not args.module.exists():
        raise FileNotFoundError(args.module)
    if not (args.commands_dir / "create_command").exists():
        raise FileNotFoundError(args.commands_dir / "create_command")

    run_dir = args.out_dir / args.run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    metrics = MigrationMetrics(
        run_id=args.run_id,
        src_arch=platform.machine(),
        dst_arch=platform.machine(),
        same_arch=True,
        notes=f"host_simplest_example;module={args.module.name}",
        profile_name="host",
    )
    snapshots: list[ProcessSnapshot] = []
    source_pid = 0
    dest_pid = 0
    total_start = time.monotonic_ns()

    try:
        source_pid, source_ipc, source_log, source_main, source_checkpoint = (
            launch_server(
                commands_dir=args.commands_dir,
                module_path=args.module,
                run_dir=run_dir,
                label="source",
            )
        )
        snapshots.append(snapshot_local(source_pid, "host-source-ready"))
        run(
            [str(args.commands_dir / "start_command"), str(source_ipc)],
            cwd=args.commands_dir,
        )
        time.sleep(max(0.0, args.warmup_seconds))
        snapshots.append(snapshot_local(source_pid, "host-source-running"))
        checkpoint(
            commands_dir=args.commands_dir,
            ipc=source_ipc,
            log=source_log,
            main_memory=source_main,
            checkpoint_memory=source_checkpoint,
            wait_timeout=args.timeout_seconds,
        )
        snapshots.append(snapshot_local(source_pid, "host-source-after-checkpoint"))
        source_events = event_times(source_log)
        metrics.checkpoint_ms = elapsed_ms(
            source_events,
            "request_server - checkpoint start",
            "request_server - checkpoint completed",
        )
        metrics.final_dump_ms = metrics.checkpoint_ms

        archive = run_dir / "wasm-state.tar.gz"
        metrics.archive_bytes = archive_state(archive, source_main, source_checkpoint)

        transfer_start = time.monotonic_ns()
        dest_archive = run_dir / "dest-wasm-state.tar.gz"
        shutil.copy2(archive, dest_archive)
        metrics.transfer_ms = monotonic_elapsed_ms(transfer_start)
        restored_main, restored_checkpoint = restore_archive(
            dest_archive, run_dir / "restored-state"
        )

        restore_start = time.monotonic_ns()
        dest_pid, dest_ipc, dest_log, dest_main, dest_checkpoint = launch_server(
            commands_dir=args.commands_dir,
            module_path=args.module,
            run_dir=run_dir,
            label="dest",
        )
        shutil.copy2(restored_main, dest_main)
        shutil.copy2(restored_checkpoint, dest_checkpoint)
        snapshots.append(snapshot_local(dest_pid, "host-dest-ready"))
        run(
            [str(args.commands_dir / "start_command"), str(dest_ipc)],
            cwd=args.commands_dir,
        )
        wait_for_log(
            dest_log,
            "request_server - restore memory completed",
            timeout_s=args.timeout_seconds,
        )
        metrics.restore_ms = monotonic_elapsed_ms(restore_start)
        snapshots.append(snapshot_local(dest_pid, "host-dest-restored"))
        wait_for_log(
            dest_log, "request_server - end of call", timeout_s=args.timeout_seconds
        )
        metrics.success = dest_main.exists() and dest_checkpoint.exists()
    finally:
        for pid in (source_pid, dest_pid):
            if pid:
                subprocess.run(
                    ["kill", "-9", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    metrics.total_ms = monotonic_elapsed_ms(total_start)
    metrics.downtime_ms = (
        metrics.checkpoint_ms + metrics.transfer_ms + metrics.restore_ms
    )
    if metrics.transfer_ms > 0:
        metrics.bandwidth_mbps = (metrics.archive_bytes * 8) / (
            metrics.transfer_ms * 1000
        )
    metrics.timestamp = datetime.now().isoformat()
    write_metrics(metrics, args.csv)
    write_snapshots(run_dir / "process_snapshots.json", snapshots)

    print(f"run_id={metrics.run_id}")
    print(f"module={args.module.name}")
    print(f"success={metrics.success}")
    print(
        f"checkpoint_ms={metrics.checkpoint_ms} transfer_ms={metrics.transfer_ms} "
        f"restore_ms={metrics.restore_ms} downtime_ms={metrics.downtime_ms}"
    )
    print(f"archive_bytes={metrics.archive_bytes}")
    print(f"artifacts={run_dir}")
    print(f"csv={args.csv}")
    return 0 if metrics.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
