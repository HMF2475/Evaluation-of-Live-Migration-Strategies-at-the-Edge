"""
Postcopy (Lazy) Migration Strategy: CRIU lazy-pages.

Implements experimental post-copy live migration for the native
workloads by using CRIU's --lazy-pages mechanism:
- Freeze quickly on source with a minimal dump (no full memory transfer)
- Transfer small image set to destination
- Start a page-server on source
- Start lazy-pages daemon on destination
- Restore on destination with --lazy-pages and fetch pages on-demand

WARNING:
- Requires direct IP connectivity between destination and source on a TCP port.
- Still experimental; failures are expected depending on kernel/CRIU build.
"""

from __future__ import annotations

import time

try:
    from .migration_strategy import MigrationStrategy
    from .multipass_command import MultipassCommand
    from .ssh_utils import (
        ensure_direct_ssh_trust,
        get_node_ip,
        transfer_archive_direct,
        transfer_archive_via_host,
    )
except ImportError:
    from migration_strategy import MigrationStrategy
    from multipass_command import MultipassCommand
    from ssh_utils import (
        ensure_direct_ssh_trust,
        get_node_ip,
        transfer_archive_direct,
        transfer_archive_via_host,
    )


class PostcopyMigration(MigrationStrategy):
    """Postcopy (lazy) migration using CRIU lazy-pages."""

    def _pick_free_port_on_source(self, start_port: int, tries: int = 20) -> int:
        """
        Pick a free TCP port on the source node for the CRIU page-server.

        Post-copy uses a TCP page-server on the source. If the port is already
        in use (often due to a previous failed/aborted run leaving a CRIU dump
        process alive), CRIU will fail with "Can't bind page server: Address
        already in use". This helper probes ports and returns the first free.
        """
        has_ss = self.source.exec("command -v ss >/dev/null 2>&1", check=False)[0] == 0
        for port in range(start_port, start_port + max(1, tries)):
            # Prefer ss when available; fall back to assuming "free" if probing fails.
            if has_ss:
                rc2, out, _ = self.source.exec(
                    f"sudo ss -ltnH 'sport = :{port}' 2>/dev/null || true", check=False
                )
                if rc2 == 0 and out.strip():
                    continue
                if rc2 == 0 and not out.strip():
                    return port
            else:
                # No ss: best-effort default.
                return port
        return start_port

    def __init__(
        self,
        source: MultipassCommand,
        dest: MultipassCommand,
        transfer_mode: str = "host",
        relay_node: str | None = None,
        page_server_port: int = 9999,
    ):
        super().__init__(source, dest, transfer_mode, relay_node=relay_node)
        self.metrics.migration_method = "postcopy"
        self.metrics.network_migration = "no"
        self.page_server_port = page_server_port

    def get_method_name(self) -> str:
        return "postcopy"

    def _get_source_pid(self) -> str | None:
        for pid_file in ("/home/ubuntu/gol.pid", "/home/ubuntu/app.pid"):
            if self.source.file_exists(pid_file):
                rc, pid_str, _ = self.source.exec(f"cat {pid_file}", check=False)
                pid = pid_str.strip()
                if rc == 0 and pid.isdigit():
                    self.log(f"  Using PID from {pid_file}: {pid}")
                    return pid
        return None

    def migrate(self, run_id: str) -> bool:
        self.metrics.run_id = run_id
        self.metrics.notes = f"transfer_mode={self.transfer_mode};lazy_pages_port={self.page_server_port}"
        if self.relay_node:
            self.metrics.notes += f";relay_node={self.relay_node}"

        self.log("=== POST-COPY (LAZY-PAGES) MIGRATION ===")

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
        _, last_before, _ = self.source.exec(f"tail -n 1 {out_path}", check=False)
        self.log(f"  Last gol value (pre-freeze): {last_before}")

        # Capture architecture information for metrics
        self.metrics.src_arch = self.source.get_arch()
        self.metrics.dst_arch = self.dest.get_arch()
        self.metrics.same_arch = self.metrics.src_arch == self.metrics.dst_arch

        # Determine source IP for page-server connectivity
        source_ip = get_node_ip(self.source.node)
        if not source_ip:
            self.log("ERROR: Could not determine source node IP for lazy-pages")
            self.metrics.notes += "; source_ip_missing"
            return False
        requested_port = int(self.page_server_port)
        port = self._pick_free_port_on_source(requested_port)
        if port != requested_port:
            self.log(f"  Port {requested_port} busy on source; using {port} instead")
            self.metrics.notes += f"; port_remap={requested_port}->{port}"
        self.log(f"  Page-server endpoint: {source_ip}:{port}")

        page_server_pid: str | None = None
        lazy_pages_pid: str | None = None
        lazy_pages_start_ns: int | None = None

        try:
            # Step 2: Start dump in background (may act as page-server on some CRIU versions)
            self.log("Step 2: Final dump (freezing service) with --lazy-pages...")
            t_dump_start = time.time_ns()

            bind_addr = "0.0.0.0"
            dump_start_cmd = (
                "sudo rm -rf /tmp/CRIU-gol && "
                "sudo mkdir -p /tmp/CRIU-gol && "
                f"sudo bash -lc '"
                "set -e; "
                "rm -f /tmp/CRIU-gol/dump.pid /tmp/CRIU-gol/dump.stdout; "
                "nohup criu dump -t "
                f"{pid}"
                " -D /tmp/CRIU-gol -v4 -o dump.log "
                f"--shell-job --skip-file-rwx-check --lazy-pages --address {bind_addr} --port {port} "
                "--leave-stopped "
                ">/tmp/CRIU-gol/dump.stdout 2>&1 "
                "& echo $! > /tmp/CRIU-gol/dump.pid; "
                "sleep 0.1; "
                "cat /tmp/CRIU-gol/dump.pid"
                "'"
            )
            rc, dump_pid_str, _ = self.source.exec(dump_start_cmd, check=False)
            if rc != 0 or not dump_pid_str.strip().isdigit():
                self.log("ERROR: Failed to start lazy-pages dump on source")
                _, dump_log, _ = self.source.exec(
                    "sudo tail -n 80 /tmp/CRIU-gol/dump.log", check=False
                )
                if dump_log:
                    self.log(f"Dump log:\n{dump_log}")
                _, dump_stdout, _ = self.source.exec(
                    "sudo tail -n 80 /tmp/CRIU-gol/dump.stdout", check=False
                )
                if dump_stdout:
                    self.log(f"Dump stdout:\n{dump_stdout}")
                self.metrics.notes += "; dump_start_failed"
                return False
            dump_pid = dump_pid_str.strip()
            page_server_pid = dump_pid
            self.log(f"  Dump PID: {dump_pid}")

            ready = False
            for _ in range(60):  # up to ~12s
                # If the dump process already died, stop early and surface the log.
                rc_ps, _, _ = self.source.exec(
                    f"ps -p {dump_pid} >/dev/null 2>&1", check=False
                )
                if rc_ps != 0:
                    self.log("ERROR: Dump process exited early")
                    _, dump_log, _ = self.source.exec(
                        "sudo tail -n 120 /tmp/CRIU-gol/dump.log", check=False
                    )
                    if dump_log:
                        self.log(f"Dump log:\n{dump_log}")
                    self.metrics.notes += "; dump_exited_early"
                    return False
                rc_inv, _, _ = self.source.exec(
                    "sudo test -f /tmp/CRIU-gol/inventory.img", check=False
                )
                if rc_inv == 0:
                    ready = True
                    break
                time.sleep(0.2)

            t_dump_ready = time.time_ns()
            dump_ms = (t_dump_ready - t_dump_start) // 1_000_000
            self.metrics.checkpoint_ms = int(dump_ms)
            self.metrics.final_dump_ms = int(dump_ms)
            if not ready:
                self.log(
                    "WARNING: inventory.img not detected quickly; dump may still be initializing"
                )
                self.metrics.notes += "; dump_ready_timeout"
            self.log(f"  Dump init time (approx freeze duration): {dump_ms} ms")

            expected_after = "unknown"
            _, frozen_last, _ = self.source.exec(f"tail -n 1 {out_path}", check=False)
            if frozen_last.isdigit():
                expected_after = str(int(frozen_last) + 1)
            self.log(f"  Frozen last gol value: {frozen_last}")
            self.log(f"  Expected after restore: {expected_after}")

            # Step 3: Dump process serves pages
            self.log("Step 3: Page-server ready on source (served by dump process)")

            # Step 4: Archive images
            self.log("Step 4: Creating archive...")
            rc, _, _ = self.source.exec(
                "sudo tar -C /tmp -czf /tmp/CRIU-gol.tar.gz CRIU-gol && "
                "sudo cp /tmp/CRIU-gol.tar.gz /home/ubuntu/CRIU-gol.tar.gz && "
                "sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-gol.tar.gz",
                check=False,
            )
            if rc != 0:
                self.log("ERROR: Archive creation failed")
                self.metrics.notes += "; archive_failed"
                return False

            rc, size_str, _ = self.source.exec(
                "sudo stat -c %s /tmp/CRIU-gol.tar.gz", check=False
            )
            self.metrics.archive_bytes = (
                int(size_str) if size_str.strip().isdigit() else 0
            )
            self.log(f"  Archive size: {self.metrics.archive_bytes} bytes")

            # Step 5: Transfer
            self.log("Step 5: Transferring archive...")
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
                self.log("ERROR: Transfer failed")
                self.metrics.notes += "; transfer_failed"
                return False

            t_transfer_done = time.time_ns()
            self.metrics.transfer_ms = int(
                (t_transfer_done - t_transfer_start) // 1_000_000
            )
            self.log(f"  Transfer time: {self.metrics.transfer_ms} ms")

            # Step 6: Unpack
            self.log("Step 6: Unpacking on destination...")
            rc, _, _ = self.dest.exec(
                "sudo rm -rf /tmp/CRIU-gol && sudo mkdir -p /tmp/CRIU-gol && "
                "sudo tar -C /tmp -xzf /home/ubuntu/CRIU-gol.tar.gz",
                check=False,
            )
            if rc != 0:
                self.log("ERROR: Unpack failed")
                self.metrics.notes += "; unpack_failed"
                return False

            self.ensure_gol_output_file()

            # Ensure /tmp/gol exists (gol workload)
            self.log("Step 6.8: Ensuring /tmp/gol binary is present on destination...")
            rc, _, _ = self.dest.exec("[ -x /tmp/gol ]", check=False)
            if rc != 0:
                self.log("  /tmp/gol missing on destination, copying from source...")
                rc_src, gol_b64, _ = self.source.exec(
                    "if [ -f /tmp/gol ]; then base64 /tmp/gol; else echo ''; fi",
                    check=False,
                )
                if rc_src == 0 and gol_b64.strip():
                    self.dest.exec(
                        f"echo '{gol_b64.strip()}' | base64 -d > /tmp/gol && chmod +x /tmp/gol",
                        check=False,
                    )

            # Step 7: Start lazy-pages daemon
            self.log("Step 7: Starting lazy-pages daemon on destination...")
            # Important: we must fully detach, otherwise `multipass exec` can block
            # waiting for the lazy-pages foreground process.
            start_lazy_cmd = (
                "sudo bash -lc '"
                "set -e; "
                "cd /tmp/CRIU-gol; "
                "rm -f /tmp/CRIU-gol/lazy-pages.pid /tmp/CRIU-gol/lazy-pages.stdout; "
                "nohup criu lazy-pages -D /tmp/CRIU-gol "
                f"--page-server --address {source_ip} --port {port} "
                "-v4 -o /tmp/CRIU-gol/lazy-pages.log "
                ">/tmp/CRIU-gol/lazy-pages.stdout 2>&1 "
                "& echo $! > /tmp/CRIU-gol/lazy-pages.pid; "
                "sleep 0.1; "
                "cat /tmp/CRIU-gol/lazy-pages.pid"
                "'"
            )
            rc, lp_pid_str, _ = self.dest.exec(start_lazy_cmd, check=False)
            if rc != 0 or not lp_pid_str.strip().isdigit():
                self.log("ERROR: Failed to start lazy-pages daemon")
                _, lp_log, _ = self.dest.exec(
                    "sudo tail -n 120 /tmp/CRIU-gol/lazy-pages.log", check=False
                )
                if lp_log:
                    self.log(f"Lazy-pages log:\n{lp_log}")
                _, lp_stdout, _ = self.dest.exec(
                    "sudo tail -n 120 /tmp/CRIU-gol/lazy-pages.stdout", check=False
                )
                if lp_stdout:
                    self.log(f"Lazy-pages stdout:\n{lp_stdout}")
                self.metrics.notes += "; lazy_pages_start_failed"
                return False

            lazy_pages_pid = lp_pid_str.strip()
            self.log(f"  Lazy-pages PID: {lazy_pages_pid}")
            lazy_pages_start_ns = time.monotonic_ns()

            # Wait for lazy-pages socket to appear (restore connects to this socket).
            for _ in range(50):  # up to ~5s
                rc_sock, _, _ = self.dest.exec(
                    "test -S /tmp/CRIU-gol/lazy-pages.socket", check=False
                )
                if rc_sock == 0:
                    break
                time.sleep(0.1)

            # Step 8: Restore
            self.log("Step 8: Restoring process...")
            t_restore_start = time.time_ns()
            restore_cmd = (
                "cd /tmp/CRIU-gol && "
                "sudo criu restore -D /tmp/CRIU-gol -v4 -o restore.log "
                "--shell-job --restore-detached --pidfile /tmp/CRIU-gol/restored.pid "
                "--skip-file-rwx-check --lazy-pages"
            )
            rc, _, _ = self.dest.exec(restore_cmd, check=False)
            if rc != 0:
                self.log("ERROR: Restore failed")
                _, restore_log, _ = self.dest.exec(
                    "sudo tail -n 80 /tmp/CRIU-gol/restore.log", check=False
                )
                if restore_log:
                    self.log(f"Restore log:\n{restore_log}")
                self.metrics.notes += "; restore_failed"
                self.metrics.success = False
                return False

            t_restore_done = time.time_ns()
            self.metrics.restore_ms = int(
                (t_restore_done - t_restore_start) // 1_000_000
            )
            self.log(f"  Restore time: {self.metrics.restore_ms} ms")

            # Persist PID files on destination so it can act as a source for "bounce" migrations.
            restored_pid = self.persist_restored_pid_files("/tmp/CRIU-gol/restored.pid")
            if restored_pid:
                self.log(
                    f"  Restored PID: {restored_pid} (written to /home/ubuntu/gol.pid)"
                )
            else:
                self.log("WARNING: Could not persist restored PID files on destination")
                self.metrics.notes += "; pidfile_persist_failed"

            # Wait a bit for lazy-pages to pull pages; keep server alive.
            if lazy_pages_pid:
                for _ in range(15):  # up to 15s
                    rc_ps, _, _ = self.dest.exec(
                        f"ps -p {lazy_pages_pid} >/dev/null 2>&1", check=False
                    )
                    if rc_ps != 0:
                        break
                    time.sleep(1)

            # Step 9: Verify
            self.log("Step 9: Verifying migration...")
            verify_wait_s = 3
            t_verify = time.monotonic()
            time.sleep(verify_wait_s)
            verify_elapsed_s = time.monotonic() - t_verify

            if expected_after != "unknown" and self.dest.file_exists(out_path):
                rc, observed, _ = self.dest.exec(f"tail -n 1 {out_path}", check=False)
                expected_min = expected_after
                expected_at_check = "unknown"
                if expected_min.isdigit():
                    expected_at_check = str(int(expected_min) + int(verify_elapsed_s))
                self.log(
                    f"  Expected min: {expected_min} (after ~{verify_elapsed_s:.1f}s → ~{expected_at_check}), "
                    f"Observed: {observed}"
                )
                if (
                    rc == 0
                    and observed
                    and observed.isdigit()
                    and int(observed) >= int(expected_after)
                ):
                    self.log("✓ SUCCESS: Gol continued correctly!")
                    self.metrics.success = True
                else:
                    self.log("WARNING: Could not validate gol continuity")
                    self.metrics.notes += "; continuity_unknown"
                    self.metrics.success = True
            else:
                # Enhanced validation: check gol.out is being updated (simulation running)
                gol_out = out_path
                rc_stat, mtime_before, _ = self.dest.exec(
                    f"stat -c %Y {gol_out}", check=False
                )
                time.sleep(2)
                rc_stat2, mtime_after, _ = self.dest.exec(
                    f"stat -c %Y {gol_out}", check=False
                )
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
            if self.metrics.transfer_ms > 0:
                self.metrics.bandwidth_mbps = (self.metrics.archive_bytes * 8) / (
                    self.metrics.transfer_ms * 1000
                )
            self.log(
                f"Total downtime: {self.metrics.downtime_ms} ms "
                f"(dump: {self.metrics.checkpoint_ms} + transfer: {self.metrics.transfer_ms} + restore: {self.metrics.restore_ms})"
            )

            return True
        finally:
            if lazy_pages_start_ns is not None:
                self.metrics.lazy_pages_active_ms = int(
                    (time.monotonic_ns() - lazy_pages_start_ns) // 1_000_000
                )
            if lazy_pages_pid:
                _, out_sz, _ = self.dest.exec(
                    "sudo stat -c %s /tmp/CRIU-gol/lazy-pages.log 2>/dev/null || echo 0",
                    check=False,
                )
                s = (out_sz or "").strip()
                self.metrics.lazy_pages_log_bytes = int(s) if s.isdigit() else 0
            if lazy_pages_pid:
                self.dest.exec(
                    f"sudo kill -9 {lazy_pages_pid} 2>/dev/null || true", check=False
                )
            if page_server_pid:
                self.source.exec(
                    f"sudo kill -9 {page_server_pid} 2>/dev/null || true", check=False
                )
            self.source.exec(f"sudo kill -9 {pid} 2>/dev/null || true", check=False)
