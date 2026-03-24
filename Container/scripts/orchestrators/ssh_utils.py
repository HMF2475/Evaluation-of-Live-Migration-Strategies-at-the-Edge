"""
SSH and file transfer utilities for direct VM-to-VM migration.

Handles Ed25519 key generation, trust setup, and SCP transfers.
"""

import subprocess
import os
from typing import Optional
from pathlib import Path

try:
    from .multipass_command import MultipassCommand
except ImportError:
    from multipass_command import MultipassCommand


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
    
    # Generate Ed25519 key pair on source if not exists
    rc, _, _ = source.exec("test -f ~/.ssh/id_ed25519", check=False)
    if rc != 0:
        print("  Generating Ed25519 key pair on source...")
        source.exec(
            'ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "CRIU-migration"',
            check=False
        )
    
    # Get public key from source
    rc, pubkey, _ = source.exec("cat ~/.ssh/id_ed25519.pub", check=False)
    if rc != 0 or not pubkey:
        print("ERROR: Could not read public key from source")
        return False
    
    # Add public key to destination's authorized_keys
    print("  Adding public key to destination's authorized_keys...")
    dest.exec(
        f'mkdir -p ~/.ssh && chmod 700 ~/.ssh && '
        f'echo "{pubkey}" >> ~/.ssh/authorized_keys && '
        f'chmod 600 ~/.ssh/authorized_keys',
        check=False
    )
    
    # Test SSH connection
    print("  Testing SSH connectivity...")
    rc, _, _ = source.exec(
        f'ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null '
        f'ubuntu@{dest_ip} "echo OK"',
        check=False
    )
    
    if rc == 0:
        print("  ✓ SSH trust ready")
        return True
    else:
        print("ERROR: SSH test failed")
        return False


def transfer_archive_direct(source_node: str, dest_node: str, 
                           source_path: str, dest_path: str) -> bool:
    """Transfer file source→destination directly via SCP.
    
    Uses direct SSH connection between VMs without going through
    the host machine.
    
    Args:
        source_node: Source VM name
        dest_node: Destination VM name
        source_path: Full path on source VM
        dest_path: Full path on destination VM
        
    Returns:
        True if transfer succeeded, False otherwise
    """
    source = MultipassCommand(source_node)
    dest_ip = get_node_ip(dest_node)
    
    if not dest_ip:
        print(f"ERROR: Could not get IP for {dest_node}")
        return False
    
    print(f"  Transferring {source_path} via SCP...")
    rc, _, _ = source.exec(
        f'scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null '
        f'{source_path} ubuntu@{dest_ip}:{dest_path}',
        check=False
    )
    
    return rc == 0


def transfer_archive_via_host(source_node: str, dest_node: str,
                             source_path: str, dest_path: str) -> bool:
    """Transfer file source→host→destination using multipass transfer.
    
    Uses the host machine as intermediate storage, suitable when
    direct VM-to-VM networking is not available.
    
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
    
    print(f"  Transferring {source_path} via host...")
    
    # Transfer from source to host
    rc, _, _ = source.exec(f"test -f {source_path}", check=False)
    if rc != 0:
        print(f"ERROR: Source file not found: {source_path}")
        return False
    
    # Use multipass transfer
    temp_file = f"/tmp/{source_path.split('/')[-1]}"
    rc = subprocess.run(
        ["multipass", "transfer", f"{source_node}:{source_path}", temp_file],
        capture_output=True
    ).returncode
    
    if rc != 0:
        print(f"ERROR: Transfer from source failed")
        return False
    
    # Transfer from host to destination
    rc = subprocess.run(
        ["multipass", "transfer", temp_file, f"{dest_node}:{dest_path}"],
        capture_output=True
    ).returncode
    
    # Cleanup
    try:
        os.remove(temp_file)
    except FileNotFoundError:
        pass
    
    return rc == 0
