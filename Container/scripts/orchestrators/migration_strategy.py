"""
Abstract base class for migration strategies.

Defines the interface that all migration implementations must follow.
"""

from abc import ABC, abstractmethod
from datetime import datetime

try:
    from .metrics import MigrationMetrics
    from .multipass_command import MultipassCommand
except ImportError:
    from metrics import MigrationMetrics
    from multipass_command import MultipassCommand


class MigrationStrategy(ABC):
    """Abstract base class for migration strategies.
    
    Subclasses must implement the specific migration logic for
    cold, precopy, postcopy, etc.
    """
    
    def __init__(
        self,
        source: MultipassCommand,
        dest: MultipassCommand,
        transfer_mode: str = "host",
        network_migration: bool = False,
        ext_net_map: str | None = None,
    ):
        """Initialize migration strategy.
        
        Args:
            source: Source node command executor
            dest: Destination node command executor
            transfer_mode: "host" for host-mediated or "direct" for SCP
            network_migration: If True, enable CRIU TCP/socket options and mark metrics as networked
            ext_net_map: Optional CRIU ext-net-map (e.g. "SRC_IP:DST_IP")
        """
        self.source = source
        self.dest = dest
        self.transfer_mode = transfer_mode
        self.network_migration = network_migration
        self.ext_net_map = ext_net_map
        self.metrics = MigrationMetrics(run_id="")

    @staticmethod
    def counter_output_path() -> str:
        """Path where the counter workload's stdout is redirected."""
        return "/home/ubuntu/counter.out"

    def ensure_counter_output_file(self) -> None:
        """Ensure the counter output file exists on destination.

        This prevents CRIU restore from failing when stdout/stderr point to a
        regular file (created by the workload launcher via shell redirection).
        We intentionally do NOT transfer file contents to avoid duplicated
        values when comparing outputs from both nodes.
        """
        path = self.counter_output_path()
        self.dest.exec(f"touch {path} && chmod 664 {path}", check=False)

    @staticmethod
    def counter_pid_paths() -> tuple[str, str]:
        """PID files used by the workload launcher and orchestrators."""
        return ("/home/ubuntu/counter.pid", "/home/ubuntu/app.pid")

    def persist_restored_pid_files(self, restored_pidfile: str = "/tmp/CRIU-counter/restored.pid") -> str | None:
        """Write restored PID into /home/ubuntu/counter.pid and /home/ubuntu/app.pid on destination.

        This makes the restored workload discoverable for subsequent migrations (e.g. "bounce" tests).

        Returns:
            PID string if successfully written, otherwise None.
        """
        counter_pid, app_pid = self.counter_pid_paths()
        cmd = (
            "sudo bash -lc '"
            "set -e; "
            f"pid=$(cat {restored_pidfile}); "
            "test -n \"$pid\"; "
            f"echo \"$pid\" > {counter_pid}; "
            f"cp {counter_pid} {app_pid}; "
            f"chown ubuntu:ubuntu {counter_pid} {app_pid}; "
            "echo \"$pid\""
            "'"
        )
        rc, out, _ = self.dest.exec(cmd, check=False)
        pid = out.strip().splitlines()[-1] if out else ""
        if rc == 0 and pid.isdigit():
            return pid
        return None
    
    def log(self, msg: str):
        """Print timestamped log message."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}")
    
    @abstractmethod
    def migrate(self, run_id: str) -> bool:
        """Execute the migration.
        
        Args:
            run_id: Unique identifier for this run
            
        Returns:
            True if migration succeeded, False otherwise
        """
        pass
    
    @abstractmethod
    def get_method_name(self) -> str:
        """Return migration method name (cold, precopy, postcopy)."""
        pass
    
    def finalize_metrics(self):
        """Finalize metrics with timestamp."""
        self.metrics.timestamp = datetime.now().isoformat()
