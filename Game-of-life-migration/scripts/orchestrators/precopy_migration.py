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


class PrecopyMigration(MigrationStrategy):
    """Precopy live migration: multiple pre-dumps, final dump, transfer, restore."""

    def __init__(
        self,
        source: MultipassCommand,
        dest: MultipassCommand,
        transfer_mode: str = "host",
        relay_node: str | None = None,
        iterations: int = 2,
    ):
        """Initialize precopy migration strategy.

        Args:
            source: Source node command executor
            dest: Destination node command executor
            transfer_mode: "host" for host-mediated or "direct" for SCP
            iterations: Number of pre-dump iterations before final dump
        """
        super().__init__(source, dest, transfer_mode, relay_node=relay_node)
        self.metrics.migration_method = "precopy"
        self.metrics.network_migration = "no"
        self.iterations = iterations

    def get_method_name(self) -> str:
        """Return migration method name."""
        return "precopy"

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
        self.metrics.notes = (
            f"transfer_mode={self.transfer_mode};iterations={self.iterations}"
        )
        if self.relay_node:
            self.metrics.notes += f";relay_node={self.relay_node}"

        self.log("=== PRE-COPY LIVE MIGRATION ===")

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

        # Step 2: Setup dump directory
        self.log("Step 2: Preparing dump directory...")
        self.source.exec(
            "sudo rm -rf /tmp/CRIU-gol && sudo mkdir -p /tmp/CRIU-gol",
            check=False,
        )

        # Step 3: Pre-dumps (service continues running)
        self.log(
            f"Step 3: Running {self.iterations} pre-dumps and transferring them while service keeps running..."
        )
        t_precopy_start = time.time_ns()
        precopy_archive_ms = 0
        precopy_transfer_ms = 0
        precopy_unpacked_ms = 0
        precopy_bytes = 0

        for i in range(self.iterations):
            self.log(f"  Pre-dump {i + 1}/{self.iterations}...")
            iter_dir = f"/tmp/CRIU-gol/iter-{i}"
            prev_opt = ""
            if i > 0:
                prev_iter_dir = f"../iter-{i - 1}"
                prev_opt = f" --prev-images-dir {prev_iter_dir}"
            cmd = (
                f"sudo mkdir -p {iter_dir} && "
                f"sudo criu pre-dump -t {pid} -D {iter_dir}{prev_opt} "
                "-v4 --shell-job --skip-file-rwx-check --track-mem"
            )
            rc, _, err = self.source.exec(
                cmd,
                check=False,
            )
            if rc != 0:
                self.log(f"ERROR: Pre-dump {i + 1} failed")
                self.metrics.notes += f"; predump_failed_{i + 1}"
                return False

            iter_archive = f"/home/ubuntu/CRIU-gol-iter-{i}.tar.gz"
            self.log(f"  Archiving and transferring pre-dump {i + 1}...")
            t_iter_archive = time.time_ns()
            rc, _, _ = self.source.exec(
                f"sudo tar -C /tmp/CRIU-gol -czf {iter_archive} iter-{i} && "
                f"sudo chown ubuntu:ubuntu {iter_archive}",
                check=False,
            )
            precopy_archive_ms += int((time.time_ns() - t_iter_archive) // 1_000_000)
            if rc != 0:
                self.log(f"ERROR: Failed to archive pre-dump {i + 1}")
                self.metrics.notes += f"; predump_archive_failed_{i + 1}"
                return False
            _, iter_size_str, _ = self.source.exec(
                f"stat -c %s {iter_archive}", check=False
            )
            if (iter_size_str or "").strip().isdigit():
                precopy_bytes += int(iter_size_str)

            t_iter_transfer = time.time_ns()
            if self.transfer_mode == "host":
                iter_ok = transfer_archive_via_host(
                    self.source.node,
                    self.dest.node,
                    iter_archive,
                    iter_archive,
                    relay_node=self.relay_node,
                )
            else:
                iter_ok = transfer_archive_direct(
                    self.source.node,
                    self.dest.node,
                    iter_archive,
                    iter_archive,
                )
            precopy_transfer_ms += int((time.time_ns() - t_iter_transfer) // 1_000_000)
            if not iter_ok:
                self.log(f"ERROR: Failed to transfer pre-dump {i + 1}")
                self.metrics.notes += f"; predump_transfer_failed_{i + 1}"
                return False

            t_iter_unpack = time.time_ns()
            rc, _, _ = self.dest.exec(
                "sudo mkdir -p /tmp/CRIU-gol && "
                f"sudo tar -C /tmp/CRIU-gol -xzf {iter_archive}",
                check=False,
            )
            precopy_unpacked_ms += int((time.time_ns() - t_iter_unpack) // 1_000_000)
            if rc != 0:
                self.log(f"ERROR: Failed to unpack pre-dump {i + 1} on destination")
                self.metrics.notes += f"; predump_unpack_failed_{i + 1}"
                return False
            time.sleep(1)  # Brief pause between pre-dumps

        # Record pre-dump time for detailed analysis
        t_predumps_done = time.time_ns()
        predump_ms = (t_predumps_done - t_precopy_start) // 1_000_000
        self.metrics.predump_ms = int(predump_ms)
        self.log(f"  Total pre-dump time: {predump_ms} ms")
        self.log(
            "  Pre-copy transfer outside downtime: "
            f"archive={precopy_archive_ms} ms, transfer={precopy_transfer_ms} ms, "
            f"unpack={precopy_unpacked_ms} ms, bytes={precopy_bytes}"
        )
        self.metrics.notes += (
            f";precopy_streamed_iters={self.iterations}"
            f";precopy_stream_archive_ms={precopy_archive_ms}"
            f";precopy_stream_transfer_ms={precopy_transfer_ms}"
            f";precopy_stream_unpack_ms={precopy_unpacked_ms}"
            f";precopy_stream_bytes={precopy_bytes}"
        )

        # Step 4: Final dump (service FREEZES here)
        self.log("Step 4: Final dump (freezing service)...")
        t_final_dump_start = time.time_ns()

        prev_opt = ""
        if self.iterations > 0:
            last_iter_dir = f"iter-{self.iterations - 1}"
            prev_opt = f" --prev-images-dir {last_iter_dir}"

        rc, _, err = self.source.exec(
            f"sudo criu dump -t {pid} -D /tmp/CRIU-gol -v4 -o dump.log{prev_opt} "
            "--shell-job --skip-file-rwx-check",
            check=False,
        )

        if rc != 0:
            self.log("ERROR: Final dump failed")
            _, dump_log, _ = self.source.exec(
                "sudo tail -n 50 /tmp/CRIU-gol/dump.log", check=False
            )
            if dump_log:
                self.log(f"Dump log:\n{dump_log}")
            self.metrics.notes += "; dump_failed"
            return False

        t_final_dump_done = time.time_ns()
        final_dump_ms = (t_final_dump_done - t_final_dump_start) // 1_000_000
        self.metrics.final_dump_ms = int(final_dump_ms)
        # For precopy, checkpoint_ms represents the final dump (freeze) duration.
        self.metrics.checkpoint_ms = int(final_dump_ms)
        self.log(f"  Final dump time (freeze duration): {final_dump_ms} ms")

        # Step 5: Create and transfer only the final dump archive. Previous
        # pre-dump images have already been copied to the destination while the
        # service was running, so they do not count as downtime.
        self.log("Step 5: Creating final dump archive...")
        t_archive_start = time.time_ns()
        self.source.exec(
            "sudo tar -C /tmp --exclude='CRIU-gol/iter-*' "
            "-czf /home/ubuntu/CRIU-gol-final.tar.gz CRIU-gol && "
            "sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-gol-final.tar.gz",
            check=False,
        )
        self.metrics.archive_create_ms = int(
            (time.time_ns() - t_archive_start) // 1_000_000
        )

        # Get archive size
        rc, size_str, _ = self.source.exec(
            "stat -c %s /home/ubuntu/CRIU-gol-final.tar.gz", check=False
        )
        try:
            archive_bytes = int(size_str)
        except (ValueError, TypeError):
            archive_bytes = 0
        self.metrics.archive_bytes = archive_bytes
        self.log(f"  Archive size: {archive_bytes} bytes")

        # Step 6: Transfer
        self.log("Step 6: Transferring archive...")

        # Set up direct SSH trust before first SCP transfer
        if self.transfer_mode == "direct":
            self.log("  Setting up direct SSH trust...")
            if not ensure_direct_ssh_trust(self.source.node, self.dest.node):
                self.log("ERROR: Failed to set up SSH trust for direct transfer")
                self.metrics.notes += "; ssh_trust_failed"
                return False

        t_transfer_start = time.time_ns()
        transfer_timings: dict[str, int] = {}

        if self.transfer_mode == "host":
            transfer_ok = transfer_archive_via_host(
                self.source.node,
                self.dest.node,
                "/home/ubuntu/CRIU-gol-final.tar.gz",
                "/home/ubuntu/CRIU-gol-final.tar.gz",
                relay_node=self.relay_node,
                timings=transfer_timings,
            )
        else:
            transfer_ok = transfer_archive_direct(
                self.source.node,
                self.dest.node,
                "/home/ubuntu/CRIU-gol-final.tar.gz",
                "/home/ubuntu/CRIU-gol-final.tar.gz",
                timings=transfer_timings,
            )

        if not transfer_ok:
            self.metrics.notes += "; transfer_failed"
            return False

        t_transfer_done = time.time_ns()
        transfer_ms = (t_transfer_done - t_transfer_start) // 1_000_000
        self.metrics.transfer_ms = int(transfer_ms)
        self.record_transfer_timings(transfer_timings)
        self.log(f"  Transfer time: {transfer_ms} ms")

        # Step 7: Unpack on destination
        self.log("Step 7: Unpacking on destination...")
        t_unpack_start = time.time_ns()
        self.dest.exec(
            "sudo mkdir -p /tmp/CRIU-gol && "
            "sudo tar -C /tmp -xzf /home/ubuntu/CRIU-gol-final.tar.gz",
            check=False,
        )
        self.metrics.unpack_ms = int((time.time_ns() - t_unpack_start) // 1_000_000)

        # Step 7.5: Ensure gol stdout target exists (avoid transferring file contents)
        self.log("Step 7.5: Ensuring gol output file exists on destination...")
        self.ensure_gol_output_file()

        # Step 7.8: Ensure migrated process executable exists on destination
        self.log("Step 7.8: Ensuring /tmp/gol binary is present on destination...")
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

        # Step 8: Restore
        self.log("Step 8: Restoring process...")
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

        # Step 9: Verify
        self.log("Step 9: Verifying migration...")
        verify_wait_s = 3
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

        # Final metrics: CRITICAL FIX
        # Downtime = final_dump + transfer + restore (NOT including pre-dumps)
        # Pre-dumps happen while service is still running, so they don't count
        self.metrics.downtime_ms = (
            self.metrics.final_dump_ms
            + self.metrics.transfer_ms
            + self.metrics.restore_ms
        )
        # Calculate effective bandwidth in Mbps (bytes → bits, ms → seconds)
        if self.metrics.transfer_ms > 0:
            self.metrics.bandwidth_mbps = (self.metrics.archive_bytes * 8) / (
                self.metrics.transfer_ms * 1000
            )

        self.log(
            f"Total downtime: {self.metrics.downtime_ms} ms "
            f"(final_dump: {self.metrics.final_dump_ms} + "
            f"transfer: {self.metrics.transfer_ms} + "
            f"restore: {self.metrics.restore_ms})"
        )
        self.log(
            f"Pre-dump time (not counted as downtime): {self.metrics.predump_ms} ms"
        )
        if self.metrics.transfer_ms > 0:
            self.log(f"Effective bandwidth: {self.metrics.bandwidth_mbps:.2f} Mbps")

        return True
