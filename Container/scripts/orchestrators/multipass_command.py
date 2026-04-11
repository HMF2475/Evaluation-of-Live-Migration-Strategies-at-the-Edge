"""
Multipass VM command execution wrapper.

Provides interface for running commands on Multipass VMs.
"""

import subprocess
from typing import Tuple


class MultipassCommand:
    """Wrapper for multipass exec commands."""

    def __init__(self, node: str):
        """Initialize Multipass command executor.

        Args:
            node: Multipass VM name (e.g., 'edge-node-1')
        """
        self.node = node

    def exec(
        self, cmd: str, sudo: bool = False, check: bool = True
    ) -> Tuple[int, str, str]:
        """Execute command on node and return (returncode, stdout, stderr).

        Args:
            cmd: Command to execute
            sudo: If True, prepend 'sudo' to command
            check: If True, raise on non-zero exit

        Returns:
            Tuple of (returncode, stdout_str, stderr_str)
        """
        if sudo:
            bash_cmd = f"set -e; sudo {cmd}"
        else:
            bash_cmd = cmd

        result = subprocess.run(
            ["multipass", "exec", self.node, "--", "bash", "-lc", bash_cmd],
            capture_output=True,
            text=True,
        )

        if check and result.returncode != 0:
            print(f"[ERROR] {self.node}: {result.stderr}")
            return result.returncode, result.stdout, result.stderr

        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def get_arch(self) -> str:
        """Get node architecture."""
        _, arch, _ = self.exec("uname -m")
        return arch.strip()

    def test_criu(self) -> bool:
        """Check if CRIU is available and working."""
        rc, output, _ = self.exec("/usr/bin/criu --version", check=False)
        if rc == 0:
            return True
        rc, output, _ = self.exec("criu --version", check=False)
        return rc == 0

    def test_process_running(self, pid: str) -> bool:
        """Check if process with given PID is running."""
        rc, _, _ = self.exec(f"kill -0 {pid}", check=False)
        return rc == 0

    def file_exists(self, path: str) -> bool:
        """Check if file exists."""
        rc, _, _ = self.exec(f"test -f {path}", check=False)
        return rc == 0

    def dir_exists(self, path: str) -> bool:
        """Check if directory exists."""
        rc, _, _ = self.exec(f"test -d {path}", check=False)
        return rc == 0
