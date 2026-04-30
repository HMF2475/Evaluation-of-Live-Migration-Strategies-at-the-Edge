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
        relay_node: str | None = None,
    ):
        """Initialize migration strategy.

        Args:
            source: Source node command executor
            dest: Destination node command executor
            transfer_mode: "host" for host-mediated or "direct" for SCP
            relay_node: Optional third VM used as intermediate hop for host-mode transfers
        """
        self.source = source
        self.dest = dest
        self.transfer_mode = transfer_mode
        self.relay_node = relay_node
        self.metrics = MigrationMetrics(run_id="")

    def record_transfer_timings(self, timings: dict[str, int]) -> None:
        """Copy detailed transfer timing fields into the metrics object."""
        for key in (
            "transfer_setup_ms",
            "transfer_send_ms",
            "transfer_receive_ms",
            "transfer_cleanup_ms",
        ):
            setattr(self.metrics, key, int(timings.get(key, 0)))

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
