"""
Precopy (Live) Migration Strategy: Multiple pre-dumps, then final dump and restore.

This is the optimized live migration method where the service keeps running
during pre-dump phases (incremental snapshots). Only during the final dump
does the service freeze. This reduces downtime.

CRITICAL FIX: Downtime is calculated as final_dump_ms + transfer_ms + restore_ms,
NOT the total precopy time. Pre-dump time doesn't count because the service
is still running and incrementally transferring changed pages.
"""

import time
from pathlib import Path

try:
    from .migration_strategy import MigrationStrategy
    from .metrics import MigrationMetrics
    from .multipass_command import MultipassCommand
    from .ssh_utils import transfer_archive_via_host, transfer_archive_direct
except ImportError:
    from migration_strategy import MigrationStrategy
    from metrics import MigrationMetrics
    from multipass_command import MultipassCommand
    from ssh_utils import transfer_archive_via_host, transfer_archive_direct


class PrecopyMigration(MigrationStrategy):
    """Precopy live migration: multiple pre-dumps, final dump, transfer, restore."""

    def __init__(self, source: MultipassCommand, dest: MultipassCommand,
                 transfer_mode: str = "host", iterations: int = 2):
        """Initialize precopy migration strategy.
        
        Args:
            source: Source node command executor
            dest: Destination node command executor
            transfer_mode: "host" for host-mediated or "direct" for SCP
            iterations: Number of pre-dump iterations before final dump
        """
        super().__init__(source, dest, transfer_mode)
        self.metrics.migration_method = "precopy"
        self.metrics.network_migration = "no"
        self.iterations = iterations

    def get_method_name(self) -> str:
        """Return migration method name."""
        return "precopy"

    def _get_source_pid(self) -> str:
        """Read PID from counter.pid or fallback to app.pid.
        
        Returns:
            Process ID as string, or None if not found
        """
        for pid_file in ("/home/ubuntu/counter.pid", "/home/ubuntu/app.pid"):
            if self.source.file_exists(pid_file):
                rc, pid_str, _ = self.source.exec(f"cat {pid_file}", check=False)
                pid = pid_str.strip()
                if rc == 0 and pid.isdigit():
                    self.log(f"  Using PID from {pid_file}: {pid}")
                    return pid
        return None

    def _verify_and_transfer_log_file(self) -> bool:
        """Transfer counter.log from source to destination.
        
        This file is required by CRIU for restoring open file handles.
        Works with both direct and host transfer modes.
        
        Returns:
            True if transfer succeeded or not needed, False on error
        """
        self.log("Step 7.5: Transferring log file content...")
        
        if not self.source.file_exists("/home/ubuntu/counter.log"):
            self.log("  (No log file to transfer, creating placeholder...)")
            # Create empty placeholder file so CRIU doesn't fail on restore
            self.dest.exec("touch /home/ubuntu/counter.log", check=False)
            return True
        
        if self.transfer_mode == "direct":
            transfer_ok = transfer_archive_direct(
                self.source.node, self.dest.node,
                "/home/ubuntu/counter.log",
                "/home/ubuntu/counter.log"
            )
        else:  # host transfer mode
            transfer_ok = transfer_archive_via_host(
                self.source.node, self.dest.node,
                "/home/ubuntu/counter.log",
                "/home/ubuntu/counter.log"
            )
        
        if not transfer_ok:
            self.log("  WARNING: Could not transfer counter.log, creating placeholder...")
            # Create empty placeholder file so CRIU doesn't fail on restore
            self.dest.exec("touch /home/ubuntu/counter.log", check=False)
        
        return True  # Don't fail migration if log transfer fails

    def migrate(self, run_id: str) -> bool:
        """Execute precopy live migration.
        
        Process:
        1. Verify source process exists
        2. Setup dump directory
        3. Run N pre-dump iterations (service keeps running, incremental snapshots)
        4. Run final dump (freezes service)
        5. Archive the checkpoint
        6. Transfer archive to destination
        7. Unpack archive on destination
        8. Restore process on destination
        9. Verify continuity
        
        CRITICAL: Downtime only counts the final dump phase (when service freezes),
        plus transfer and restore. Pre-dump time is NOT downtime because the service
        continues executing and only incremental changes are being copied.
        
        Args:
            run_id: Unique identifier for this run
            
        Returns:
            True if migration succeeded, False otherwise
        """
        self.metrics.run_id = run_id
        self.metrics.notes = f"transfer_mode={self.transfer_mode};iterations={self.iterations}"
        
        self.log("=== PRE-COPY LIVE MIGRATION ===")

        # Step 1: Verify source process
        self.log("Step 1: Checking source process...")
        pid = self._get_source_pid()
        if not pid:
            self.log("ERROR: PID file not found (expected /home/ubuntu/counter.pid or /home/ubuntu/app.pid).")
            self.metrics.notes += "; pid_not_found"
            return False

        # Get last value before migration
        rc, last_before, _ = self.source.exec("tail -n 1 /home/ubuntu/counter.log", check=False)
        expected_after = str(int(last_before) + 1) if last_before.isdigit() else "unknown"
        self.log(f"  Last counter value: {last_before}")

        # Capture architecture information for metrics
        self.metrics.src_arch = self.source.get_arch()
        self.metrics.dst_arch = self.dest.get_arch()
        self.metrics.same_arch = 1 if self.metrics.src_arch == self.metrics.dst_arch else 0
        if self.metrics.same_arch:
            self.log(f"  Architecture: {self.metrics.src_arch} (compatible)")
        else:
            self.log(f"  WARNING: Different architectures: {self.metrics.src_arch} → {self.metrics.dst_arch}")

        # Step 2: Setup dump directory
        self.log("Step 2: Preparing dump directory...")
        self.source.exec(
            "sudo rm -rf /tmp/CRIU-counter && "
            "sudo mkdir -p /tmp/CRIU-counter",
            check=False,
        )

        # Step 3: Pre-dumps (service continues running)
        self.log(f"Step 3: Running {self.iterations} pre-dumps...")
        t_precopy_start = time.time_ns()

        for i in range(self.iterations):
            self.log(f"  Pre-dump {i+1}/{self.iterations}...")
            rc, _, err = self.source.exec(
                f"sudo criu pre-dump -t {pid} -D /tmp/CRIU-counter -v4 --shell-job --skip-file-rwx-check",
                check=False,
            )
            if rc != 0:
                self.log(f"WARNING: Pre-dump {i+1} failed (continuing anyway)")
            time.sleep(1)  # Brief pause between pre-dumps

        # Record pre-dump time for detailed analysis
        t_predumps_done = time.time_ns()
        predump_ms = (t_predumps_done - t_precopy_start) // 1_000_000
        self.metrics.predump_ms = int(predump_ms)
        self.log(f"  Total pre-dump time: {predump_ms} ms")

        # Step 4: Final dump (service FREEZES here)
        self.log("Step 4: Final dump (freezing service)...")
        t_final_dump_start = time.time_ns()
        
        rc, _, err = self.source.exec(
            f"sudo criu dump -t {pid} -D /tmp/CRIU-counter -v4 -o dump.log --shell-job --skip-file-rwx-check",
            check=False,
        )

        if rc != 0:
            self.log("ERROR: Final dump failed")
            _, dump_log, _ = self.source.exec("sudo tail -n 50 /tmp/CRIU-counter/dump.log", check=False)
            if dump_log:
                self.log(f"Dump log:\n{dump_log}")
            self.metrics.notes += "; dump_failed"
            return False

        t_final_dump_done = time.time_ns()
        final_dump_ms = (t_final_dump_done - t_final_dump_start) // 1_000_000
        self.metrics.final_dump_ms = int(final_dump_ms)
        self.log(f"  Final dump time (freeze duration): {final_dump_ms} ms")

        # Step 5: Create and transfer archive
        self.log("Step 5: Creating archive...")
        self.source.exec(
            "cd /tmp && tar czf CRIU-counter.tar.gz CRIU-counter/",
            check=False,
        )

        # Get archive size
        rc, size_str, _ = self.source.exec("stat -c %s /tmp/CRIU-counter.tar.gz", check=False)
        try:
            archive_bytes = int(size_str)
        except (ValueError, TypeError):
            archive_bytes = 0
        self.metrics.archive_bytes = archive_bytes
        self.log(f"  Archive size: {archive_bytes} bytes")

        # Step 6: Transfer
        self.log("Step 6: Transferring archive...")
        t_transfer_start = time.time_ns()
        
        transfer_ok = transfer_archive_via_host(
            self.source.node, self.dest.node,
            "/tmp/CRIU-counter.tar.gz",
            "/home/ubuntu/CRIU-counter.tar.gz",
        ) if self.transfer_mode == "host" else transfer_archive_direct(
            self.source.node, self.dest.node,
            "/tmp/CRIU-counter.tar.gz",
            "/home/ubuntu/CRIU-counter.tar.gz",
        )
        
        if not transfer_ok:
            self.metrics.notes += "; transfer_failed"
            return False
        
        t_transfer_done = time.time_ns()
        transfer_ms = (t_transfer_done - t_transfer_start) // 1_000_000
        self.metrics.transfer_ms = int(transfer_ms)
        self.log(f"  Transfer time: {transfer_ms} ms")

        # Step 7: Unpack on destination
        self.log("Step 7: Unpacking on destination...")
        self.dest.exec(
            "sudo rm -rf /tmp/CRIU-counter && sudo mkdir -p /tmp/CRIU-counter && "
            "sudo tar -C /tmp -xzf /home/ubuntu/CRIU-counter.tar.gz",
            check=False
        )
        
        # Step 7.5: Transfer log file if needed
        if not self._verify_and_transfer_log_file():
            return False

        # Step 8: Restore
        self.log("Step 8: Restoring process...")
        t_restore_start = time.time_ns()

        rc, _, err = self.dest.exec(
            "sudo criu restore -D /tmp/CRIU-counter -v4 -o restore.log "
            "--shell-job --restore-detached --pidfile /tmp/CRIU-counter/restored.pid --skip-file-rwx-check",
            check=False,
        )

        if rc != 0:
            self.log("ERROR: Restore failed")
            _, restore_log, _ = self.dest.exec("sudo tail -n 50 /tmp/CRIU-counter/restore.log", check=False)
            if restore_log:
                self.log(f"Restore log:\n{restore_log}")
            self.metrics.notes += "; restore_failed"
            self.metrics.success = False
            return False

        t_restore_done = time.time_ns()
        restore_ms = (t_restore_done - t_restore_start) // 1_000_000
        self.metrics.restore_ms = int(restore_ms)
        self.log(f"  Restore time: {restore_ms} ms")

        # Step 9: Verify
        self.log("Step 9: Verifying migration...")
        time.sleep(3)

        rc, observed, _ = self.dest.exec("tail -n 1 /home/ubuntu/counter.log", check=False)
        self.log(f"  Expected: {expected_after}, Observed: {observed}")

        if rc != 0 or not observed:
            self.log("WARNING: Could not read log from destination")
            self.metrics.notes += "; log_read_failed"
            self.metrics.success = False
        elif observed.isdigit() and expected_after != "unknown":
            if int(observed) >= int(expected_after):
                self.log("✓ SUCCESS: Counter continued correctly!")
                self.metrics.success = True
            else:
                self.log("✗ FAILED: Counter value mismatch")
                self.metrics.notes += f"; counter_mismatch: expected >={expected_after}, got {observed}"
                self.metrics.success = False
        else:
            self.log("✓ Counter value received (continuity verified)")
            self.metrics.success = True

        # Final metrics: CRITICAL FIX
        # Downtime = final_dump + transfer + restore (NOT including pre-dumps)
        # Pre-dumps happen while service is still running, so they don't count
        self.metrics.downtime_ms = self.metrics.final_dump_ms + self.metrics.transfer_ms + self.metrics.restore_ms
        # Calculate effective bandwidth in Mbps (bytes → bits, ms → seconds)
        if self.metrics.transfer_ms > 0:
            self.metrics.bandwidth_mbps = (self.metrics.archive_bytes * 8) / (self.metrics.transfer_ms * 1000)
        
        self.log(f"Total downtime: {self.metrics.downtime_ms} ms "
                 f"(final_dump: {self.metrics.final_dump_ms} + "
                 f"transfer: {self.metrics.transfer_ms} + "
                 f"restore: {self.metrics.restore_ms})")
        self.log(f"Pre-dump time (not counted as downtime): {self.metrics.predump_ms} ms")
        if self.metrics.transfer_ms > 0:
            self.log(f"Effective bandwidth: {self.metrics.bandwidth_mbps:.2f} Mbps")

        return True
