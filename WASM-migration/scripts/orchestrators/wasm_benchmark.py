#!/usr/bin/env python3
"""Run one Tinto WASM checkpoint/restore migration between two Multipass nodes.

Flow:
1. deploy host-built `create/start/migrate_command` binaries and one injected WASM module;
2. start source request server and activate computation;
3. ask source to checkpoint into `main_memory.b` + `checkpoint_memory.b`;
4. copy checkpoint archive to destination (host/relay or direct VM-to-VM);
5. start destination request server, seed memory files, restore, and wait for completion;
6. write CRIU-compatible CSV metrics plus `/proc/<pid>` snapshots.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WASM_ROOT = SCRIPT_DIR.parents[1]
REPO_ROOT = SCRIPT_DIR.parents[2]
CONTAINER_ORCH = REPO_ROOT / "Container" / "scripts" / "orchestrators"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.append(str(CONTAINER_ORCH))

from metrics import MigrationMetrics, write_metrics  # noqa: E402
from process_metrics import ProcessSnapshot, snapshot_remote, write_snapshots  # noqa: E402
from ssh_utils import transfer_archive_direct, transfer_archive_via_host  # noqa: E402


PID_RE = re.compile(r"server_pid=(\d+)")
LOG_RE = re.compile(r"^(?P<event>.+) - (?P<sec>\d+) sec - (?P<nsec>\d+) nsec$")
REMOTE_BASE = Path("/home/ubuntu/wasm-migration")
DEFAULT_MODULE = (
    WASM_ROOT / "wasm-migrate-commands" / "wasm_test_computation" / "3mm_with_cr.wasm"
)
DEFAULT_COMMANDS_DIR = WASM_ROOT / "wasm-migrate-commands" / "build"


def q(value: str | Path) -> str:
    return shlex.quote(str(value))


def mp_exec(node: str, cmd: str, *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["multipass", "exec", node, "--", "bash", "-lc", cmd],
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"{node}: command failed ({result.returncode}): {cmd}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def mp_transfer(source: str | Path, dest: str) -> None:
    result = subprocess.run(
        ["multipass", "transfer", str(source), dest], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"multipass transfer failed: {source} -> {dest}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def monotonic_elapsed_ms(start_ns: int) -> int:
    delta_ns = max(0, time.monotonic_ns() - start_ns)
    return ns_to_ms_ceil(delta_ns)


def ns_to_ms_ceil(delta_ns: int) -> int:
    if delta_ns == 0:
        return 0
    return max(1, int((delta_ns + 999_999) // 1_000_000))


def ns_to_us_ceil(delta_ns: int) -> int:
    if delta_ns == 0:
        return 0
    return max(1, int((delta_ns + 999) // 1_000))


def event_times_from_text(text: str) -> dict[str, int]:
    """Parse injected `request_server.c` timestamp lines into nanoseconds."""
    events: dict[str, int] = {}
    for line in text.splitlines():
        match = LOG_RE.match(line.strip())
        if not match:
            continue
        events[match.group("event")] = int(match.group("sec")) * 1_000_000_000 + int(
            match.group("nsec")
        )
    return events


def elapsed_ns(events: dict[str, int], start: str, end: str) -> int:
    if start not in events or end not in events:
        return 0
    return max(0, events[end] - events[start])


def remote_file_text(node: str, path: Path) -> str:
    result = mp_exec(node, f"cat {q(path)}", check=False)
    return result.stdout if result.returncode == 0 else ""


def wait_remote_log(node: str, path: Path, needle: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    needle_q = q(needle)
    path_q = q(path)
    while time.monotonic() < deadline:
        result = mp_exec(node, f"grep -Fq {needle_q} {path_q}", check=False)
        if result.returncode == 0:
            return
        time.sleep(0.1)
    raise TimeoutError(f"{node}: timed out waiting for {needle!r} in {path}")


def wait_remote_files(node: str, paths: list[Path], timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    test_expr = " && ".join(f"test -s {q(path)}" for path in paths)
    while time.monotonic() < deadline:
        if mp_exec(node, test_expr, check=False).returncode == 0:
            return
        time.sleep(0.1)
    missing = ", ".join(str(path) for path in paths)
    raise TimeoutError(f"{node}: timed out waiting for files: {missing}")


def parse_pid(text: str, label: str) -> int:
    match = PID_RE.search(text)
    if not match:
        raise RuntimeError(f"{label}: create_command did not print server_pid")
    return int(match.group(1))


def get_arch(node: str) -> str:
    result = mp_exec(node, "uname -m", check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def validate_local_payload(commands_dir: Path, module_path: Path) -> None:
    for binary in ("create_command", "start_command", "migrate_command"):
        path = commands_dir / binary
        if not path.exists():
            raise FileNotFoundError(path)
    if not module_path.exists():
        raise FileNotFoundError(module_path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remote_payload_matches(
    node: str, remote_path: Path, sha_path: Path, sha: str
) -> bool:
    result = mp_exec(
        node,
        f"test -f {q(remote_path)} && test -f {q(sha_path)} && grep -qx {q(sha)} {q(sha_path)}",
        check=False,
    )
    return result.returncode == 0


def deploy_payload(node: str, commands_dir: Path, module_path: Path) -> Path:
    remote_bin = REMOTE_BASE / "bin"
    remote_modules = REMOTE_BASE / "modules"
    mp_exec(node, f"mkdir -p {q(remote_bin)} {q(remote_modules)}")
    for binary in ("create_command", "start_command", "migrate_command"):
        local_path = commands_dir / binary
        remote_path = remote_bin / binary
        sha_path = remote_bin / f"{binary}.sha256"
        sha = file_sha256(local_path)
        if not remote_payload_matches(node, remote_path, sha_path, sha):
            mp_transfer(local_path, f"{node}:{remote_path}")
            mp_exec(node, f"printf '%s\\n' {q(sha)} > {q(sha_path)}")

    remote_module = remote_modules / module_path.name
    module_sha_path = remote_modules / f"{module_path.name}.sha256"
    module_sha = file_sha256(module_path)
    if not remote_payload_matches(node, remote_module, module_sha_path, module_sha):
        mp_transfer(module_path, f"{node}:{remote_module}")
        mp_exec(node, f"printf '%s\\n' {q(module_sha)} > {q(module_sha_path)}")

    mp_exec(
        node,
        f"chmod +x {q(remote_bin / 'create_command')} {q(remote_bin / 'start_command')} {q(remote_bin / 'migrate_command')}",
    )
    return remote_module


def cleanup_run(node: str, run_dir: Path) -> None:
    mp_exec(
        node,
        "pkill -9 -x create_command 2>/dev/null || true; "
        f"rm -rf {q(run_dir)}; mkdir -p {q(run_dir)}",
        check=False,
    )


def launch_server(
    *,
    node: str,
    run_dir: Path,
    module_path: Path,
    label: str,
    timeout_s: float,
) -> tuple[int, Path, Path, Path, Path]:
    """Create one request server and wait until it is listening on its IPC file."""
    remote_bin = REMOTE_BASE / "bin"
    ipc = run_dir / f"{label}.ipc"
    log = run_dir / f"{label}.log"
    main_memory = run_dir / f"{label}_main_memory.b"
    checkpoint_memory = run_dir / f"{label}_checkpoint_memory.b"
    create_out = run_dir / f"{label}_create.out"
    create_err = run_dir / f"{label}_create.err"
    mp_exec(
        node,
        f": > {q(ipc)}; : > {q(log)}; "
        f"cd {q(REMOTE_BASE)} && "
        f"{q(remote_bin / 'create_command')} {q(module_path)} {q(ipc)} "
        f"{q(main_memory)} {q(checkpoint_memory)} - {q(log)} "
        f"> {q(create_out)} 2> {q(create_err)}",
    )
    pid = parse_pid(remote_file_text(node, create_out), f"{node}:{label}")
    wait_remote_log(node, log, "request_server - wait for activation", timeout_s)
    return pid, ipc, log, main_memory, checkpoint_memory


def activate_and_checkpoint(
    *,
    node: str,
    ipc: Path,
    log: Path,
    main_memory: Path,
    checkpoint_memory: Path,
    warmup_seconds: float,
    timeout_s: float,
) -> None:
    """Activate source and request checkpoint inside one remote shell.

    Keeping start_command and migrate_command in one `multipass exec` avoids
    host-side CLI latency between activation and checkpoint request. That
    latency can be longer than the tiny PolyBench test modules.
    """
    remote_bin = REMOTE_BASE / "bin"
    warmup = max(0.0, warmup_seconds)
    mp_exec(
        node,
        f"cd {q(REMOTE_BASE)} && "
        f"{q(remote_bin / 'start_command')} {q(ipc)} && "
        f"sleep {warmup:.6f} && "
        f"{q(remote_bin / 'migrate_command')} {q(ipc)}",
    )
    wait_remote_log(node, log, "request_server - checkpoint completed", timeout_s)
    wait_remote_files(node, [main_memory, checkpoint_memory], timeout_s)


def archive_source_state(
    node: str, run_dir: Path, main_memory: Path, checkpoint_memory: Path
) -> tuple[Path, int]:
    state_dir = run_dir / "state"
    archive = run_dir / "wasm-state.tar.gz"
    mp_exec(
        node,
        f"rm -rf {q(state_dir)}; mkdir -p {q(state_dir)}; "
        f"cp {q(main_memory)} {q(state_dir / 'main_memory.b')}; "
        f"cp {q(checkpoint_memory)} {q(state_dir / 'checkpoint_memory.b')}; "
        f"tar -C {q(state_dir)} -czf {q(archive)} main_memory.b checkpoint_memory.b",
    )
    stat = mp_exec(node, f"stat -c %s {q(archive)}", check=False).stdout.strip()
    archive_bytes = int(stat) if stat.isdigit() else 0
    if archive_bytes == 0:
        raise RuntimeError(f"{node}: archive not created at {archive}")
    return archive, archive_bytes


def transfer_state(
    *,
    source: str,
    dest: str,
    archive: Path,
    dest_archive: Path,
    transfer_mode: str,
    relay_node: str | None,
) -> bool:
    """Move checkpoint archive using same host/direct helpers as CRIU benchmarks."""
    if transfer_mode == "direct":
        return transfer_archive_direct(source, dest, str(archive), str(dest_archive))
    return transfer_archive_via_host(
        source,
        dest,
        str(archive),
        str(dest_archive),
        relay_node=relay_node,
    )


def seed_destination(
    node: str, run_dir: Path, archive: Path, main_memory: Path, checkpoint_memory: Path
) -> None:
    """Extract transferred state and place files where destination server expects them."""
    state_dir = run_dir / "restored-state"
    mp_exec(
        node,
        f"rm -rf {q(state_dir)}; mkdir -p {q(state_dir)}; "
        f"tar -C {q(state_dir)} -xzf {q(archive)}; "
        f"cp {q(state_dir / 'main_memory.b')} {q(main_memory)}; "
        f"cp {q(state_dir / 'checkpoint_memory.b')} {q(checkpoint_memory)}",
    )


def download_run_artifacts(
    source: str, dest: str, remote_run_dir: Path, local_run_dir: Path
) -> None:
    local_run_dir.mkdir(parents=True, exist_ok=True)
    files = [
        ("source", source, "source.log"),
        ("source", source, "source_create.out"),
        ("source", source, "source_create.err"),
        ("dest", dest, "dest.log"),
        ("dest", dest, "dest_create.out"),
        ("dest", dest, "dest_create.err"),
    ]
    for prefix, node, name in files:
        target = (
            local_run_dir / f"{prefix}_{name}"
            if not name.startswith(prefix)
            else local_run_dir / name
        )
        subprocess.run(
            ["multipass", "transfer", f"{node}:{remote_run_dir / name}", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark one WASM migration between edge nodes."
    )
    parser.add_argument("--source", default="edge-node-1")
    parser.add_argument("--dest", default="edge-node-2")
    parser.add_argument(
        "--run-id", default=f"wasm-edge-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    parser.add_argument("--module", type=Path, default=DEFAULT_MODULE)
    parser.add_argument("--commands-dir", type=Path, default=DEFAULT_COMMANDS_DIR)
    parser.add_argument(
        "--csv", type=Path, default=WASM_ROOT / "metrics" / "migration_metrics.csv"
    )
    parser.add_argument(
        "--out-dir", type=Path, default=WASM_ROOT / "metrics" / "run_artifacts"
    )
    parser.add_argument("--transfer-mode", choices=["host", "direct"], default="host")
    parser.add_argument("--relay-node", default=None)
    parser.add_argument("--profile-name", default="")
    parser.add_argument("--warmup-seconds", type=float, default=0.01)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--skip-deploy", action="store_true")
    args = parser.parse_args()

    args.module = args.module.resolve()
    args.commands_dir = args.commands_dir.resolve()
    args.csv = args.csv.resolve()
    args.out_dir = args.out_dir.resolve()
    validate_local_payload(args.commands_dir, args.module)

    source_arch = get_arch(args.source)
    dest_arch = get_arch(args.dest)
    host_arch = platform.machine()
    if not args.skip_deploy and (
        source_arch and source_arch != host_arch or dest_arch and dest_arch != host_arch
    ):
        raise RuntimeError(
            f"local binaries are {host_arch}, but nodes are source={source_arch} dest={dest_arch}. "
            "Build on nodes or provide matching binaries before --skip-deploy."
        )

    remote_module = REMOTE_BASE / "modules" / args.module.name
    if not args.skip_deploy:
        remote_module = deploy_payload(args.source, args.commands_dir, args.module)
        deploy_payload(args.dest, args.commands_dir, args.module)

    remote_run_dir = REMOTE_BASE / "runs" / args.run_id
    local_run_dir = args.out_dir / args.run_id
    cleanup_run(args.source, remote_run_dir)
    cleanup_run(args.dest, remote_run_dir)

    metrics = MigrationMetrics(
        run_id=args.run_id,
        migration_method="cold",
        network_migration="no",
        src_arch=source_arch,
        dst_arch=dest_arch,
        same_arch=source_arch == dest_arch,
        notes=f"transfer_mode={args.transfer_mode};module={args.module.name}",
        profile_name=args.profile_name,
    )
    if args.relay_node and args.transfer_mode == "host":
        metrics.notes += f";relay_node={args.relay_node}"

    snapshots: list[ProcessSnapshot] = []
    source_pid = 0
    dest_pid = 0
    total_start = time.monotonic_ns()

    try:
        # SOURCE PHASE: start module and request checkpoint quickly. Some test
        # modules finish in milliseconds, so start+migrate run in one remote
        # shell instead of two separate multipass exec calls.
        source_pid, source_ipc, source_log, source_main, source_checkpoint = (
            launch_server(
                node=args.source,
                run_dir=remote_run_dir,
                module_path=remote_module,
                label="source",
                timeout_s=args.timeout_seconds,
            )
        )
        snapshots.append(snapshot_remote(args.source, source_pid, "source-ready"))
        activate_and_checkpoint(
            node=args.source,
            ipc=source_ipc,
            log=source_log,
            main_memory=source_main,
            checkpoint_memory=source_checkpoint,
            warmup_seconds=args.warmup_seconds,
            timeout_s=args.timeout_seconds,
        )
        snapshots.append(
            snapshot_remote(args.source, source_pid, "source-after-checkpoint")
        )

        source_events = event_times_from_text(remote_file_text(args.source, source_log))
        metrics.checkpoint_ns = elapsed_ns(
            source_events,
            "request_server - checkpoint start",
            "request_server - checkpoint completed",
        )
        metrics.checkpoint_us = ns_to_us_ceil(metrics.checkpoint_ns)
        metrics.checkpoint_ms = ns_to_ms_ceil(metrics.checkpoint_ns)
        metrics.final_dump_ms = metrics.checkpoint_ms

        # TRANSFER PHASE: package both WASM memory files and move archive.
        source_archive, metrics.archive_bytes = archive_source_state(
            args.source, remote_run_dir, source_main, source_checkpoint
        )
        dest_archive = remote_run_dir / "wasm-state.tar.gz"
        transfer_start = time.monotonic_ns()
        if not transfer_state(
            source=args.source,
            dest=args.dest,
            archive=source_archive,
            dest_archive=dest_archive,
            transfer_mode=args.transfer_mode,
            relay_node=args.relay_node,
        ):
            metrics.notes += ";transfer_failed"
            write_metrics(metrics, args.csv)
            return 1
        metrics.transfer_ms = monotonic_elapsed_ms(transfer_start)

        # DESTINATION PHASE: seed files before activation; start_command then restores.
        restore_start = time.monotonic_ns()
        dest_pid, dest_ipc, dest_log, dest_main, dest_checkpoint = launch_server(
            node=args.dest,
            run_dir=remote_run_dir,
            module_path=remote_module,
            label="dest",
            timeout_s=args.timeout_seconds,
        )
        seed_destination(
            args.dest, remote_run_dir, dest_archive, dest_main, dest_checkpoint
        )
        snapshots.append(snapshot_remote(args.dest, dest_pid, "dest-ready"))
        mp_exec(
            args.dest,
            f"cd {q(REMOTE_BASE)} && {q(REMOTE_BASE / 'bin' / 'start_command')} {q(dest_ipc)}",
        )
        wait_remote_log(
            args.dest,
            dest_log,
            "request_server - restore memory completed",
            args.timeout_seconds,
        )
        metrics.restore_ms = monotonic_elapsed_ms(restore_start)
        snapshots.append(snapshot_remote(args.dest, dest_pid, "dest-restored"))
        wait_remote_log(
            args.dest, dest_log, "request_server - end of call", args.timeout_seconds
        )
        metrics.success = True
    finally:
        if source_pid:
            mp_exec(
                args.source, f"kill -9 {source_pid} 2>/dev/null || true", check=False
            )
        if dest_pid:
            mp_exec(args.dest, f"kill -9 {dest_pid} 2>/dev/null || true", check=False)
        download_run_artifacts(args.source, args.dest, remote_run_dir, local_run_dir)

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
    write_snapshots(local_run_dir / "process_snapshots.json", snapshots)

    print(f"run_id={metrics.run_id}")
    print(f"source={args.source} dest={args.dest} transfer_mode={args.transfer_mode}")
    print(f"success={metrics.success}")
    print(
        f"checkpoint_ms={metrics.checkpoint_ms} transfer_ms={metrics.transfer_ms} "
        f"restore_ms={metrics.restore_ms} downtime_ms={metrics.downtime_ms}"
    )
    print(
        f"checkpoint_us={metrics.checkpoint_us} checkpoint_ns={metrics.checkpoint_ns}"
    )
    print(f"archive_bytes={metrics.archive_bytes}")
    print(f"artifacts={local_run_dir}")
    print(f"csv={args.csv}")
    return 0 if metrics.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
