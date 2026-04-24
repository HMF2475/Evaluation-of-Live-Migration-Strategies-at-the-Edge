"""
Cold Migration Strategy: Stop, checkpoint, transfer, and restore.

This is the baseline migration method where the service is stopped
before checkpointing. Downtime includes the checkpoint, transfer,
and restore phases.
"""

import time

try:
    from .migration_strategy import MigrationStrategy
    from .metrics import MigrationMetrics
    from .multipass_command import MultipassCommand
    from .ssh_utils import (
        transfer_archive_via_host,
        transfer_archive_direct,
        ensure_direct_ssh_trust,
    )
except ImportError:
    from migration_strategy import MigrationStrategy
    from multipass_command import MultipassCommand
    from ssh_utils import (
        transfer_archive_via_host,
        transfer_archive_direct,
        ensure_direct_ssh_trust,
    )


class ColdMigration(MigrationStrategy):
    """Cold migration: stop service, checkpoint, transfer, restore."""

    def __init__(
        self,
        source: MultipassCommand,
        dest: MultipassCommand,
        transfer_mode: str = "host",
        relay_node: str | None = None,
    ):
        """Initialize cold migration strategy.

        Args:
            source: Source node command executor
            dest: Destination node command executor
            transfer_mode: "host" for host-mediated or "direct" for SCP
        """
        super().__init__(source, dest, transfer_mode, relay_node=relay_node)
        self.metrics.migration_method = "cold"
        self.metrics.network_migration = "no"

    def get_method_name(self) -> str:
        """Return migration method name."""
        return "cold"

    def _get_source_pid(self) -> str:
        """Read PID from gol.pid or fallback to app.pid.

        Returns:
            Process ID as string, or None if not found
        """
        for pid_file in ("/home/ubuntu/gol.pid", "/home/ubuntu/app.pid"):
            if self.source.file_exists(pid_file):
                rc, pid_str, _ = self.source.exec(f"cat {pid_file}", check=False)
                pid = pid_str.strip()
                if rc == 0 and pid.isdigit():
                    self.log(f"  Using PID from {pid_file}: {pid}")
                    return pid
        return None

    def migrate(self, run_id: str) -> bool:
        """Execute cold migration.

        Process:
        1. Verify source process exists
        2. Checkpoint (freeze) the process with CRIU
        3. Archive the checkpoint
        4. Transfer archive to destination
        5. Unpack archive on destination
        6. Prepare destination environment
        7. Restore process on destination
        8. Verify continuity

        Args:
            run_id: Unique identifier for this run

        Returns:
            True if migration succeeded, False otherwise
        """
        self.metrics.run_id = run_id
        self.metrics.notes = f"transfer_mode={self.transfer_mode}"
        if self.relay_node:
            self.metrics.notes += f";relay_node={self.relay_node}"

        self.log("=== COLD MIGRATION ===")

        # Step 1: Verify source process
        self.log("Step 1: Checking source process...")
        pid = self._get_source_pid()
        if not pid:
            self.log(
                "ERROR: PID file not found (expected /home/ubuntu/gol.pid or /home/ubuntu/app.pid)."
            )
            self.metrics.notes += "; pid_not_found"
            return False

        out_path = self.gol_output_path()

        # Capture architecture information for metrics
        self.metrics.src_arch = self.source.get_arch()
        self.metrics.dst_arch = self.dest.get_arch()
        self.metrics.same_arch = self.metrics.src_arch == self.metrics.dst_arch
        if self.metrics.same_arch:
            self.log(f"  Architecture: {self.metrics.src_arch} (compatible)")
        else:
            self.log(
                f"  WARNING: Different architectures: {self.metrics.src_arch} → {self.metrics.dst_arch}"
            )

        # Step 2: Dump
        self.log("Step 2: Dumping process with CRIU...")
        t_checkpoint_start = time.time_ns()

        rc, _, err = self.source.exec(
            "sudo rm -rf /tmp/CRIU-gol && "
            "sudo mkdir -p /tmp/CRIU-gol && "
            f"sudo criu dump -t {pid} -D /tmp/CRIU-gol -v4 -o dump.log "
            "--shell-job --skip-file-rwx-check",
            check=False,
        )

        if rc != 0:
            self.log("ERROR: Dump failed")
            _, dump_log, _ = self.source.exec(
                "sudo tail -n 50 /tmp/CRIU-gol/dump.log", check=False
            )
            if dump_log:
                self.log(f"Dump log:\n{dump_log}")
            self.metrics.notes += "; dump_failed"
            return False

        t_checkpoint_done = time.time_ns()
        checkpoint_ms = (t_checkpoint_done - t_checkpoint_start) // 1_000_000
        self.metrics.checkpoint_ms = int(checkpoint_ms)
        self.log(f"  Checkpoint time: {checkpoint_ms} ms")

        # Step 3: Archive
        self.log("Step 3: Creating archive...")
        rc, _, _ = self.source.exec(
            "sudo tar -C /tmp -czf /tmp/CRIU-gol.tar.gz CRIU-gol && "
            "sudo cp /tmp/CRIU-gol.tar.gz /home/ubuntu/CRIU-gol.tar.gz && "
            "sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-gol.tar.gz",
        )

        if rc != 0:
            self.log("ERROR: Archive creation failed")
            self.metrics.notes += "; archive_failed"
            return False

        # Get archive size
        rc, size_str, _ = self.source.exec("sudo stat -c %s /tmp/CRIU-gol.tar.gz")
        archive_bytes = int(size_str) if size_str.isdigit() else 0
        self.metrics.archive_bytes = archive_bytes
        self.log(f"  Archive size: {archive_bytes} bytes")

        # Step 4: Transfer
        self.log("Step 4: Transferring archive...")

        # Set up direct SSH trust before first SCP transfer
        if self.transfer_mode == "direct":
            self.log("  Setting up direct SSH trust...")
            if not ensure_direct_ssh_trust(self.source.node, self.dest.node):
                self.log("ERROR: Failed to set up SSH trust for direct transfer")
                self.metrics.notes += "; ssh_trust_failed"
                return False

        t_transfer_start = time.time_ns()

        transfer_ok = (
            transfer_archive_via_host(
                self.source.node,
                self.dest.node,
                "/home/ubuntu/CRIU-gol.tar.gz",
                "/home/ubuntu/CRIU-gol.tar.gz",
                relay_node=self.relay_node,
            )
            if self.transfer_mode == "host"
            else transfer_archive_direct(
                self.source.node,
                self.dest.node,
                "/home/ubuntu/CRIU-gol.tar.gz",
                "/home/ubuntu/CRIU-gol.tar.gz",
            )
        )

        if not transfer_ok:
            self.metrics.notes += "; transfer_failed"
            return False

        t_transfer_done = time.time_ns()
        transfer_ms = (t_transfer_done - t_transfer_start) // 1_000_000
        self.metrics.transfer_ms = int(transfer_ms)
        self.log(f"  Transfer time: {transfer_ms} ms")

        # Step 5: Unpack on destination
        self.log("Step 5: Unpacking on destination...")
        rc, _, _ = self.dest.exec(
            "sudo rm -rf /tmp/CRIU-gol && "
            "sudo mkdir -p /tmp/CRIU-gol && "
            "sudo tar -C /tmp -xzf /home/ubuntu/CRIU-gol.tar.gz",
        )

        if rc != 0:
            self.log("ERROR: Unpack failed")
            self.metrics.notes += "; unpack_failed"
            return False

        # Step 6: Prepare destination
        self.log("Step 6: Preparing restore environment...")

        # Create log and output files if they don't exist
        rc, _, _ = self.dest.exec(
            "touch /home/ubuntu/gol.out && chmod 664 /home/ubuntu/gol.out"
        )

        if rc != 0:
            self.log("ERROR: Could not create log files")
            self.metrics.notes += "; prepare_failed"
            return False

        # Ensure gol stdout target exists (avoid transferring file contents)
        self.ensure_gol_output_file()

        # Step 6.5: Ensure migrated process executable exists on destination
        self.log("Step 6.5: Ensuring /tmp/gol binary is present on destination...")
        rc, _, _ = self.dest.exec("[ -x /tmp/gol ]", check=False)
        if rc != 0:
            self.log("  /tmp/gol missing on destination, copying from source...")
            rc_src, gol_b64, _ = self.source.exec(
                "if [ -f /tmp/gol ]; then base64 /tmp/gol; else echo ''; fi",
                check=False,
            )
            if rc_src != 0 or not gol_b64.strip():
                self.log(
                    "WARNING: /tmp/gol binary not found on source; restore may fail"
                )
                self.metrics.notes += "; missing_binary"
            else:
                self.dest.exec(
                    f"echo '{gol_b64.strip()}' | base64 -d > /tmp/gol && chmod +x /tmp/gol",
                    check=False,
                )

        # Step 7: Restore
        self.log("Step 7: Restoring process...")
        t_restore_start = time.time_ns()

        rc, _, err = self.dest.exec(
            "sudo criu restore -D /tmp/CRIU-gol -v4 -o restore.log "
            "--shell-job --restore-detached --pidfile /tmp/CRIU-gol/restored.pid --skip-file-rwx-check",
            check=False,
        )

        if rc != 0:
            self.log("ERROR: Restore failed")
            _, restore_log, _ = self.dest.exec(
                "sudo tail -n 50 /tmp/CRIU-gol/restore.log", check=False
            )
            if restore_log:
                self.log(f"Restore log:\n{restore_log}")
            self.metrics.notes += "; restore_failed"
            self.metrics.success = False
            return False

        t_restore_done = time.time_ns()
        restore_ms = (t_restore_done - t_restore_start) // 1_000_000
        self.metrics.restore_ms = int(restore_ms)
        self.log(f"  Restore time: {restore_ms} ms")

        # Persist PID files on destination so it can act as a source for "bounce" migrations.
        restored_pid = self.persist_restored_pid_files("/tmp/CRIU-gol/restored.pid")
        if restored_pid:
            self.log(
                f"  Restored PID: {restored_pid} (written to /home/ubuntu/gol.pid)"
            )
        else:
            self.log("WARNING: Could not persist restored PID files on destination")
            self.metrics.notes += "; pidfile_persist_failed"

        # Step 8: Verify
        self.log("Step 8: Verifying migration...")
        verify_wait_s = 3  # Give process time to write
        t_verify_start = time.monotonic()
        time.sleep(verify_wait_s)
        verify_elapsed_s = time.monotonic() - t_verify_start

        # Only check gol.out is being updated (mtime-based validation)
        gol_out = out_path
        rc_stat, mtime_before, _ = self.dest.exec(f"stat -c %Y {gol_out}", check=False)
        time.sleep(2)
        rc_stat2, mtime_after, _ = self.dest.exec(f"stat -c %Y {gol_out}", check=False)
        if (
            rc_stat == 0
            and rc_stat2 == 0
            and mtime_after.strip().isdigit()
            and mtime_before.strip().isdigit()
            and int(mtime_after) > int(mtime_before)
        ):
            self.log(
                f"✓ gol.out updated after migration (mtime {mtime_before} → {mtime_after}), simulation running!"
            )
            self.metrics.success = True
        else:
            self.log(
                "WARNING: gol.out not updated after migration; process may not be running"
            )
            self.metrics.notes += "; gol_out_not_updating"
            self.metrics.success = False

        # Final metrics
        self.metrics.downtime_ms = (
            self.metrics.checkpoint_ms
            + self.metrics.transfer_ms
            + self.metrics.restore_ms
        )
        # Calculate effective bandwidth in Mbps (bytes → bits, ms → seconds)
        if self.metrics.transfer_ms > 0:
            self.metrics.bandwidth_mbps = (self.metrics.archive_bytes * 8) / (
                self.metrics.transfer_ms * 1000
            )
        self.log(f"Total downtime: {self.metrics.downtime_ms} ms")
        if self.metrics.transfer_ms > 0:
            self.log(f"Effective bandwidth: {self.metrics.bandwidth_mbps:.2f} Mbps")

        return True
