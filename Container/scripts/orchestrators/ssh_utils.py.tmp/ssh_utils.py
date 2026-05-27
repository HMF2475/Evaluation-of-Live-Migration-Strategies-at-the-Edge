"""
SSH and file transfer utilities for migration experiments.

Supports:
- direct VM-to-VM SCP
- classic host-mediated transfers through the host machine
- relay-node transfers through a third Multipass VM
"""

import subprocess
import os
import time
from typing import Optional
from pathlib import Path
import tempfile

try:
    from .multipass_command import MultipassCommand
except ImportError:
    from multipass_command import MultipassCommand


SCP_ATTEMPTS = 5
SCP_RETRY_DELAY_SECONDS = 20
SSH_ATTEMPTS = 2
SSH_RETRY_DELAY_SECONDS = 5
SSH_OPTIONS = (
    "-o BatchMode=yes "
    "-o ConnectTimeout=45 "
    "-o ConnectionAttempts=1 "
    "-o ServerAliveInterval=30 "
    "-o ServerAliveCountMax=4 "
    "-o StrictHostKeyChecking=no "
    "-o UserKnownHostsFile=/dev/null"
)
SSH_TRUST_PROBE = os.getenv("CRIU_SSH_TRUST_PROBE", "0") == "1"


def get_node_ip(node: str) -> Optional[str]:
    """Get first IPv4 address for a multipass node.

    Args:
        node: Multipass VM name

    Returns:
        IPv4 address or None if not found
    """
    cmd = MultipassCommand(node)
    rc, output, _ = cmd.exec("hostname -I")
    if rc == 0 and output:
        return output.split()[0]
    return None


def _run_remote_with_retries(
    node: MultipassCommand,
    command: str,
    *,
    label: str,
    attempts: int,
    delay_seconds: int,
) -> bool:
    """Run a remote shell command with retries for lossy links."""
    last_rc = 1
    for attempt in range(1, attempts + 1):
        rc, _, err = node.exec(command, check=False)
        last_rc = rc
        if rc == 0:
            return True
        if attempt < attempts:
            print(
                f"WARNING: {label} failed on attempt {attempt}/{attempts} "
                f"(rc={rc}); retrying in {delay_seconds}s"
            )
            if err:
                print(f"  stderr: {err[-300:]}")
            time.sleep(delay_seconds)
    print(f"ERROR: {label} failed after {attempts} attempts (last rc={last_rc})")
    return False


def ensure_direct_ssh_trust(source_node: str, dest_node: str) -> bool:
    """Ensure source can SSH/SCP directly to destination VM.

    Sets up Ed25519 SSH key pair and adds public key to destination's
    authorized_keys. Handles key generation and trust establishment.

    Args:
        source_node: Source VM name
        dest_node: Destination VM name

    Returns:
        True if trust is established, False otherwise
    """
    source = MultipassCommand(source_node)
    dest = MultipassCommand(dest_node)

    # Get destination IP
    dest_ip = get_node_ip(dest_node)
    if not dest_ip:
        print(f"ERROR: Could not get IP for {dest_node}")
        return False

    print(f"Setting up SSH trust: {source_node} → ubuntu@{dest_ip}")

    # Ensure an Ed25519 key pair exists without triggering ssh-keygen's
    # interactive overwrite prompt when the key was created by an earlier run.
    rc, _, _ = source.exec(
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "if [ ! -s ~/.ssh/id_ed25519 ]; then "
        "rm -f ~/.ssh/id_ed25519.pub && "
        'ssh-keygen -q -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "CRIU-migration"; '
        "elif [ ! -s ~/.ssh/id_ed25519.pub ]; then "
        "ssh-keygen -y -f ~/.ssh/id_ed25519 > ~/.ssh/id_ed25519.pub; "
        "fi",
        check=False,
    )
    if rc != 0:
        print("ERROR: Could not prepare SSH key pair on source")
        return False

    # Get public key from source
    rc, pubkey, _ = source.exec("cat ~/.ssh/id_ed25519.pub", check=False)
    if rc != 0 or not pubkey:
        print("ERROR: Could not read public key from source")
        return False

    # Add public key to destination's authorized_keys (avoid duplicates)
    print("  Adding public key to destination's authorized_keys...")
    pubkey = pubkey.strip()
    dest.exec(
        f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
        f'grep -qxF "{pubkey}" ~/.ssh/authorized_keys || echo "{pubkey}" >> ~/.ssh/authorized_keys',
        check=False,
    )

    if not SSH_TRUST_PROBE:
        print("  ✓ SSH key installed; skipping probe, transfer step will verify connectivity")
        return True

    # Test SSH connection only when explicitly requested. Under lossy network
    # profiles the probe can be less reliable than the retried SCP operation.
    print("  Testing SSH connectivity...")
    ok = _run_remote_with_retries(
        source,
        f'ssh {SSH_OPTIONS} ubuntu@{dest_ip} "echo OK"',
        label=f"SSH test {source_node}->{dest_node}",
        attempts=SSH_ATTEMPTS,
        delay_seconds=SSH_RETRY_DELAY_SECONDS,
    )

    if ok:
        print("  ✓ SSH trust ready")
    else:
        print(
            "WARNING: SSH test did not complete, continuing because the key was installed; "
            "the transfer step will retry and report the final result"
        )
    return True


def _elapsed_ms(start_ns: int) -> int:
    return max(0, int((time.time_ns() - start_ns) // 1_000_000))


def _add_timing(timings: Optional[dict[str, int]], key: str, value: int) -> None:
    if timings is not None:
        timings[key] = timings.get(key, 0) + int(value)


def transfer_archive_direct(
    source_node: str,
    dest_node: str,
    source_path: str,
    dest_path: str,
    timings: Optional[dict[str, int]] = None,
) -> bool:
    """Transfer file source→destination directly via SCP.

    Ensures SSH trust is established before transferring. Uses BatchMode
    to avoid hanging on interactive auth prompts.

    Args:
        source_node: Source VM name
        dest_node: Destination VM name
        source_path: Full path on source VM
        dest_path: Full path on destination VM

    Returns:
        True if transfer succeeded, False otherwise
    """
    source = MultipassCommand(source_node)
    setup_start = time.time_ns()
    dest_ip = get_node_ip(dest_node)

    if not dest_ip:
        _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))
        print(f"ERROR: Could not get IP for {dest_node}")
        return False

    # Ensure SSH trust is established before attempting direct SCP
    if not ensure_direct_ssh_trust(source_node, dest_node):
        _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))
        print(
            f"ERROR: Failed to establish SSH trust between {source_node} and {dest_node}"
        )
        return False
    _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))

    print(f"  Transferring {source_path} via SCP...")
    send_start = time.time_ns()
    ok = _run_remote_with_retries(
        source,
        f"scp {SSH_OPTIONS} {source_path} ubuntu@{dest_ip}:{dest_path}",
        label=f"SCP {source_node}->{dest_node}",
        attempts=SCP_ATTEMPTS,
        delay_seconds=SCP_RETRY_DELAY_SECONDS,
    )
    _add_timing(timings, "transfer_send_ms", _elapsed_ms(send_start))

    return ok


def transfer_archive_via_host(
    source_node: str,
    dest_node: str,
    source_path: str,
    dest_path: str,
    relay_node: Optional[str] = None,
    timings: Optional[dict[str, int]] = None,
) -> bool:
    """Transfer file through either the host machine or a relay VM.

    Without relay_node, this uses `multipass transfer` twice:
    source VM -> host temp file -> destination VM.

    With relay_node, this uses VM-to-VM `scp` twice:
    source VM -> relay VM -> destination VM. This keeps "host mode"
    comparable to a real intermediate hop when running experiments.

    Args:
        source_node: Source VM name
        dest_node: Destination VM name
        source_path: Full path on source VM
        dest_path: Full path on destination VM

    Returns:
        True if transfer succeeded, False otherwise
    """
    source = MultipassCommand(source_node)
    dest = MultipassCommand(dest_node)

    if relay_node:
        setup_start = time.time_ns()
        relay = MultipassCommand(relay_node)
        relay_ip = get_node_ip(relay_node)
        dest_ip = get_node_ip(dest_node)
        stage_name = Path(dest_path).name
        relay_stage = f"/tmp/{stage_name}.{source_node}.stage"

        if not relay_ip or not dest_ip:
            _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))
            print(f"ERROR: Could not get IP for relay={relay_node} or dest={dest_node}")
            return False

        print(f"  Transferring {source_path} via relay node {relay_node}...")

        rc, _, _ = source.exec(f"test -f {source_path}", check=False)
        if rc != 0:
            _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))
            print(f"ERROR: Source file not found: {source_path}")
            return False

        if not ensure_direct_ssh_trust(source_node, relay_node):
            _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))
            print(
                f"ERROR: Failed to establish SSH trust between {source_node} and {relay_node}"
            )
            return False
        if not ensure_direct_ssh_trust(relay_node, dest_node):
            _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))
            print(
                f"ERROR: Failed to establish SSH trust between {relay_node} and {dest_node}"
            )
            return False
        _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))

        send_start = time.time_ns()
        send_ok = _run_remote_with_retries(
            source,
            f"scp {SSH_OPTIONS} {source_path} ubuntu@{relay_ip}:{relay_stage}",
            label=f"SCP {source_node}->{relay_node}",
            attempts=SCP_ATTEMPTS,
            delay_seconds=SCP_RETRY_DELAY_SECONDS,
        )
        _add_timing(timings, "transfer_send_ms", _elapsed_ms(send_start))
        if not send_ok:
            print("ERROR: Transfer from source to relay failed")
            return False

        receive_start = time.time_ns()
        receive_ok = _run_remote_with_retries(
            relay,
            f"scp {SSH_OPTIONS} {relay_stage} ubuntu@{dest_ip}:{dest_path}",
            label=f"SCP {relay_node}->{dest_node}",
            attempts=SCP_ATTEMPTS,
            delay_seconds=SCP_RETRY_DELAY_SECONDS,
        )
        _add_timing(timings, "transfer_receive_ms", _elapsed_ms(receive_start))
        cleanup_start = time.time_ns()
        relay.exec(f"rm -f {relay_stage}", check=False)
        _add_timing(timings, "transfer_cleanup_ms", _elapsed_ms(cleanup_start))
        if not receive_ok:
            print("ERROR: Transfer from relay to destination failed")
            return False

        return True

    print(f"  Transferring {source_path} via host...")

    # Transfer from source to host
    setup_start = time.time_ns()
    rc, _, _ = source.exec(f"test -f {source_path}", check=False)
    if rc != 0:
        _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))
        print(f"ERROR: Source file not found: {source_path}")
        return False

    # Use a unique temp file to avoid collisions across concurrent runs
    fd, temp_file = tempfile.mkstemp(suffix=f"_{source_path.split('/')[-1]}")
    os.close(fd)
    _add_timing(timings, "transfer_setup_ms", _elapsed_ms(setup_start))
    send_start = time.time_ns()
    rc = subprocess.run(
        ["multipass", "transfer", f"{source_node}:{source_path}", temp_file],
        capture_output=True,
    ).returncode
    _add_timing(timings, "transfer_send_ms", _elapsed_ms(send_start))

    if rc != 0:
        print("ERROR: Transfer from source failed")
        cleanup_start = time.time_ns()
        try:
            os.remove(temp_file)
        except FileNotFoundError:
            pass
        _add_timing(timings, "transfer_cleanup_ms", _elapsed_ms(cleanup_start))
        return False

    # Transfer from host to destination
    receive_start = time.time_ns()
    rc = subprocess.run(
        ["multipass", "transfer", temp_file, f"{dest_node}:{dest_path}"],
        capture_output=True,
    ).returncode
    _add_timing(timings, "transfer_receive_ms", _elapsed_ms(receive_start))

    # Cleanup
    cleanup_start = time.time_ns()
    try:
        os.remove(temp_file)
    except FileNotFoundError:
        pass
    _add_timing(timings, "transfer_cleanup_ms", _elapsed_ms(cleanup_start))

    return rc == 0
