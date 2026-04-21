"""
TCP client migration with CRIU across nodes.

This implements cold / pre-copy / post-copy migrations for a *TCP client* that
maintains an established connection to a fixed server node.

Important constraint (see CRIU docs / CRIU-limitations.md):
- Restoring an established TCP connection on another host requires that the
  restored process still has the same *local IP address* it had at dump time.

To satisfy this, the workload binds the client socket to a virtual IP (VIP),
and the migration code "moves" that VIP from source -> destination between dump
and restore (plus a best-effort gratuitous ARP).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

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


@dataclass(frozen=True)
class TcpEndpoint:
    ip: str
    port: int


class TcpClientMigrationBase(MigrationStrategy):
    """
    Shared helpers for TCP client migrations.
    """

    def __init__(
        self,
        source: MultipassCommand,
        dest: MultipassCommand,
        *,
        server: MultipassCommand | None = None,
        transfer_mode: str = "host",
        relay_node: str | None = None,
    ):
        super().__init__(source, dest, transfer_mode, relay_node=relay_node)
        self.server = server
        self.metrics.network_migration = "yes"
        # Preserve a hint for plotting/analysis without changing the CSV schema
        self.metrics.notes = f"transfer_mode={transfer_mode};workload=tcp-client"
        if relay_node:
            self.metrics.notes += f";relay_node={relay_node}"

    def _get_pid(self) -> str | None:
        for pid_file in ("/home/ubuntu/tcp_client.pid", "/home/ubuntu/client.pid"):
            if self.source.file_exists(pid_file):
                rc, pid_str, _ = self.source.exec(f"cat {pid_file}", check=False)
                pid = pid_str.strip()
                if rc == 0 and pid.isdigit():
                    self.log(f"  Using PID from {pid_file}: {pid}")
                    return pid
                rc, pid_str, _ = self.source.exec(f"sudo cat {pid_file}", check=False)
                pid = pid_str.strip()
                if rc == 0 and pid.isdigit():
                    self.log(f"  Using PID from {pid_file} (sudo read): {pid}")
                    return pid
        return None

    def _read_vip(self) -> str | None:
        rc, out, _ = self.source.exec(
            "cat /home/ubuntu/tcp_vip.txt 2>/dev/null || true", check=False
        )
        vip = (out or "").strip()
        return vip if vip else None

    def _read_server_endpoint(self) -> TcpEndpoint | None:
        rc, out, _ = self.source.exec(
            "cat /home/ubuntu/tcp_server_endpoint.txt 2>/dev/null || true", check=False
        )
        s = (out or "").strip()
        if not s or ":" not in s:
            return None
        ip, port_s = s.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            return None
        if not ip:
            return None
        return TcpEndpoint(ip=ip, port=port)

    def _iface_for_route(self, node: MultipassCommand, ip: str) -> str | None:
        rc, out, _ = node.exec(
            f"ip -o route get {ip} 2>/dev/null | awk '{{for(i=1;i<=NF;i++) if($i==\"dev\") {{print $(i+1); exit}}}}'",
            check=False,
        )
        iface = (out or "").strip().splitlines()[:1]
        return iface[0] if iface else None

    def _iface_holding_vip(self, node: MultipassCommand, vip: str) -> str | None:
        # Match "<vip>/<mask>" in the addr column (4th field)
        rc, out, _ = node.exec(
            f"ip -o -4 addr show | awk -v vip='{vip}' '$4 ~ \"^\"vip\"/\" {{print $2; exit}}'",
            check=False,
        )
        iface = (out or "").strip().splitlines()[:1]
        return iface[0] if iface else None

    def _move_vip(self, *, vip: str, server_ip: str) -> bool:
        """
        Move VIP from source -> destination and emit gratuitous ARP on destination.
        """
        src_iface = self._iface_holding_vip(self.source, vip)
        dst_iface = self._iface_for_route(self.dest, server_ip)
        if not src_iface:
            self.log(
                f"ERROR: Could not find VIP {vip} on source (no matching interface)"
            )
            self.metrics.notes += "; vip_missing_on_source"
            return False
        if not dst_iface:
            self.log(
                f"ERROR: Could not determine destination interface for server {server_ip}"
            )
            self.metrics.notes += "; dest_iface_missing"
            return False

        self.log(
            f"  Moving VIP {vip} from {self.source.node}:{src_iface} -> {self.dest.node}:{dst_iface}"
        )

        # Remove from source and add to destination (best-effort)
        self.source.exec(
            f"sudo ip addr del {vip}/32 dev {src_iface} 2>/dev/null || true",
            check=False,
        )
        self.dest.exec(
            f"sudo ip addr add {vip}/32 dev {dst_iface} 2>/dev/null || true",
            check=False,
        )

        # Help ARP converge faster
        self.dest.exec(
            f"sudo arping -c 3 -A -I {dst_iface} {vip} 2>/dev/null || true", check=False
        )

        # Also force the server to re-resolve VIP -> MAC (optional)
        if self.server:
            srv_iface = self._iface_for_route(
                self.server, vip
            ) or self._iface_for_route(self.server, server_ip)
            if srv_iface:
                self.server.exec(
                    f"sudo ip neigh del {vip} dev {srv_iface} 2>/dev/null || true",
                    check=False,
                )
                self.server.exec(
                    f"ping -c 1 -W 1 {vip} >/dev/null 2>&1 || true", check=False
                )

        return True

    def _ensure_tcp_binary_on_dest(self) -> None:
        """
        Ensure /tmp/tcp-howto exists on destination (CRIU restores the same exec path).
        """
        rc, _, _ = self.dest.exec("[ -x /tmp/tcp-howto ]", check=False)
        if rc == 0:
            return
        self.log("  /tmp/tcp-howto missing on destination, copying from source...")
        rc_src, b64, _ = self.source.exec(
            "if [ -f /tmp/tcp-howto ]; then base64 /tmp/tcp-howto; else echo ''; fi",
            check=False,
        )
        if rc_src != 0 or not (b64 or "").strip():
            self.log("WARNING: /tmp/tcp-howto missing on source; restore may fail")
            self.metrics.notes += "; missing_binary"
            return
        self.dest.exec(
            f"echo '{b64.strip()}' | base64 -d > /tmp/tcp-howto && chmod +x /tmp/tcp-howto",
            check=False,
        )

    def _server_conn_count(self) -> int | None:
        if not self.server:
            return None
        rc, out, _ = self.server.exec(
            "grep -c '^New connection' /home/ubuntu/tcp_server.out 2>/dev/null || echo 0",
            check=False,
        )
        s = (out or "").strip()
        return int(s) if s.isdigit() else None

    def persist_restored_pid_files(self, restored_pidfile: str) -> str | None:  # type: ignore[override]
        """
        Persist restored PID and required state files on destination for TCP workload.

        Writes:
        - /home/ubuntu/tcp_client.pid
        - /home/ubuntu/client.pid (legacy alias)
        - /home/ubuntu/tcp_server_endpoint.txt
        - /home/ubuntu/tcp_vip.txt
        """
        # Persist PID files
        cmd = (
            "sudo bash -lc '"
            "set -e; "
            f"pid=$(cat {restored_pidfile}); "
            'test -n "$pid"; '
            'echo "$pid" > /home/ubuntu/tcp_client.pid; '
            "cp /home/ubuntu/tcp_client.pid /home/ubuntu/client.pid; "
            "chown ubuntu:ubuntu /home/ubuntu/tcp_client.pid /home/ubuntu/client.pid; "
            'echo "$pid"'
            "'"
        )
        rc, out, _ = self.dest.exec(cmd, check=False)
        pid = out.strip().splitlines()[-1] if out else ""
        if rc == 0 and pid.isdigit():
            return pid
        return None


class TcpClientColdMigration(TcpClientMigrationBase):
    def get_method_name(self) -> str:
        return "cold"

    def migrate(self, run_id: str) -> bool:
        self.metrics.run_id = run_id
        self.metrics.migration_method = "cold"
        self.metrics.technology = "CRIU"

        self.log("=== TCP CLIENT COLD MIGRATION ===")

        # Step 1: Check PID
        self.log("Step 1: Checking source client process...")
        pid = self._get_pid()
        if not pid:
            self.log(
                "ERROR: Could not read PID (expected /home/ubuntu/tcp_client.pid or /home/ubuntu/client.pid)"
            )
            rc_ls, ls_out, _ = self.source.exec(
                "ls -la /home/ubuntu | sed -n '1,120p'", check=False
            )
            if ls_out:
                self.log(f"Source /home/ubuntu listing:\n{ls_out}")
            _, tail_out, _ = self.source.exec(
                "tail -n 120 /home/ubuntu/tcp_client.out 2>/dev/null || true",
                check=False,
            )
            if tail_out:
                self.log(f"Source tcp_client.out tail:\n{tail_out}")
            self.metrics.notes += "; pid_not_found"
            return False

        endpoint = self._read_server_endpoint()
        vip = self._read_vip()
        if not endpoint or not vip:
            # Try to use last known values if available
            endpoint = getattr(self, "_last_endpoint", None) or endpoint
            vip = getattr(self, "_last_vip", None) or vip
            self.ensure_state_files_on_source(endpoint, vip)
            # Re-read after creation attempt
            endpoint = self._read_server_endpoint()
            vip = self._read_vip()
        if not endpoint or not vip:
            self.log(
                "ERROR: Missing /home/ubuntu/tcp_server_endpoint.txt or /home/ubuntu/tcp_vip.txt on source (even after recreation attempt)"
            )
            self.metrics.notes += "; missing_endpoint_or_vip"
            return False

        before_conn = self._server_conn_count()
        if before_conn is not None:
            self.log(f"  Server connections so far: {before_conn}")

        # Record arch
        self.metrics.src_arch = self.source.get_arch()
        self.metrics.dst_arch = self.dest.get_arch()
        self.metrics.same_arch = self.metrics.src_arch == self.metrics.dst_arch

        # Step 2: Dump
        self.log("Step 2: Dumping client with CRIU (--tcp-established)...")
        t_checkpoint_start = time.time_ns()
        rc, _, _ = self.source.exec(
            "sudo rm -rf /tmp/CRIU-tcp-client && sudo mkdir -p /tmp/CRIU-tcp-client && "
            f"sudo criu dump -t {pid} -D /tmp/CRIU-tcp-client -v4 -o dump.log "
            "--shell-job --skip-file-rwx-check --tcp-established",
            check=False,
        )
        t_checkpoint_done = time.time_ns()
        self.metrics.checkpoint_ms = int(
            (t_checkpoint_done - t_checkpoint_start) // 1_000_000
        )
        if rc != 0:
            self.log("ERROR: Dump failed")
            _, dump_log, _ = self.source.exec(
                "sudo tail -n 80 /tmp/CRIU-tcp-client/dump.log", check=False
            )
            if dump_log:
                self.log(f"Dump log:\n{dump_log}")
            self.metrics.notes += "; dump_failed"
            return False

        # Step 2.5: Move VIP (required for restoring established TCP on another node)
        self.log("Step 2.5: Moving VIP to destination...")
        if not self._move_vip(vip=vip, server_ip=endpoint.ip):
            return False

        # Step 3: Create archive
        self.log("Step 3: Creating archive...")
        self.source.exec(
            "sudo tar -C /tmp -czf /tmp/CRIU-tcp-client.tar.gz CRIU-tcp-client && "
            "sudo cp /tmp/CRIU-tcp-client.tar.gz /home/ubuntu/CRIU-tcp-client.tar.gz && "
            "sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-tcp-client.tar.gz",
            check=False,
        )
        _, size_str, _ = self.source.exec(
            "sudo stat -c %s /tmp/CRIU-tcp-client.tar.gz", check=False
        )
        self.metrics.archive_bytes = (
            int(size_str) if (size_str or "").strip().isdigit() else 0
        )

        # Step 4: Transfer
        self.log("Step 4: Transferring archive...")
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
                "/home/ubuntu/CRIU-tcp-client.tar.gz",
                "/home/ubuntu/CRIU-tcp-client.tar.gz",
                relay_node=self.relay_node,
            )
            if self.transfer_mode == "host"
            else transfer_archive_direct(
                self.source.node,
                self.dest.node,
                "/home/ubuntu/CRIU-tcp-client.tar.gz",
                "/home/ubuntu/CRIU-tcp-client.tar.gz",
            )
        )
        t_transfer_done = time.time_ns()
        self.metrics.transfer_ms = int(
            (t_transfer_done - t_transfer_start) // 1_000_000
        )
        if not transfer_ok:
            self.log("ERROR: Transfer failed")
            self.metrics.notes += "; transfer_failed"
            return False

        # Step 5: Unpack
        self.log("Step 5: Unpacking on destination...")
        rc, _, _ = self.dest.exec(
            "sudo rm -rf /tmp/CRIU-tcp-client && sudo mkdir -p /tmp/CRIU-tcp-client && "
            "sudo tar -C /tmp -xzf /home/ubuntu/CRIU-tcp-client.tar.gz",
            check=False,
        )
        if rc != 0:
            self.log("ERROR: Unpack failed")
            self.metrics.notes += "; unpack_failed"
            return False

        # Step 6: Prepare restore environment
        self.log("Step 6: Preparing restore environment...")
        self.dest.exec(
            "touch /home/ubuntu/tcp_client.out && chmod 664 /home/ubuntu/tcp_client.out",
            check=False,
        )
        self._ensure_tcp_binary_on_dest()

        # Step 7: Restore
        self.log("Step 7: Restoring client with CRIU (--tcp-established)...")
        t_restore_start = time.time_ns()
        rc, _, _ = self.dest.exec(
            "sudo criu restore -D /tmp/CRIU-tcp-client -v4 -o restore.log "
            "--shell-job --restore-detached --pidfile /tmp/CRIU-tcp-client/restored.pid "
            "--skip-file-rwx-check --tcp-established",
            check=False,
        )
        t_restore_done = time.time_ns()
        self.metrics.restore_ms = int((t_restore_done - t_restore_start) // 1_000_000)
        if rc != 0:
            self.log("ERROR: Restore failed")
            _, restore_log, _ = self.dest.exec(
                "sudo tail -n 120 /tmp/CRIU-tcp-client/restore.log", check=False
            )
            if restore_log:
                self.log(f"Restore log:\n{restore_log}")
            self.metrics.notes += "; restore_failed"
            self.metrics.success = False
            return False

        # Persist PID files for potential "bounce" experiments
        restored_pid = self.persist_restored_pid_files(
            "/tmp/CRIU-tcp-client/restored.pid"
        )
        if restored_pid:
            self.log(f"  Restored PID: {restored_pid}")

        # Step 8: Verify
        self.log("Step 8: Verifying connection continuity...")
        time.sleep(3)
        rc, last_line, _ = self.dest.exec(
            "tail -n 1 /home/ubuntu/tcp_client.out 2>/dev/null || true", check=False
        )
        self.log(f"  Client output (tail): {last_line}")

        # Check socket still established to server port
        rc_ss, ss_out, _ = self.dest.exec(
            f"ss -tn | grep -q ':{endpoint.port} ' && echo OK || echo NO", check=False
        )
        ok_sock = "OK" in (ss_out or "")

        after_conn = self._server_conn_count()
        if after_conn is not None:
            self.log(f"  Server connections so far: {after_conn}")

        if (
            before_conn is not None
            and after_conn is not None
            and after_conn != before_conn
        ):
            self.log(
                "✗ FAILED: Server accepted a new connection after restore (connection was not preserved)"
            )
            self.metrics.notes += f"; server_new_conn: {before_conn}->{after_conn}"
            self.metrics.success = False
        elif not ok_sock:
            self.log(
                "✗ FAILED: Destination does not show an established TCP socket to the server"
            )
            self.metrics.notes += "; tcp_not_established"
            self.metrics.success = False
        else:
            self.log("✓ SUCCESS: Client restored and TCP socket appears established")
            self.metrics.success = True

        self.metrics.downtime_ms = (
            self.metrics.checkpoint_ms
            + self.metrics.transfer_ms
            + self.metrics.restore_ms
        )
        if self.metrics.transfer_ms > 0:
            self.metrics.bandwidth_mbps = (self.metrics.archive_bytes * 8) / (
                self.metrics.transfer_ms * 1000
            )
        self.log(f"Total downtime: {self.metrics.downtime_ms} ms")

        return self.metrics.success


class TcpClientPrecopyMigration(TcpClientMigrationBase):
    def __init__(
        self,
        source: MultipassCommand,
        dest: MultipassCommand,
        *,
        server: MultipassCommand | None = None,
        transfer_mode: str = "host",
        relay_node: str | None = None,
        iterations: int = 2,
    ):
        super().__init__(
            source,
            dest,
            server=server,
            transfer_mode=transfer_mode,
            relay_node=relay_node,
        )
        self.iterations = iterations

    def get_method_name(self) -> str:
        return "precopy"

    def migrate(self, run_id: str) -> bool:
        self.metrics.run_id = run_id
        self.metrics.migration_method = "precopy"
        self.metrics.technology = "CRIU"
        self.metrics.notes += f";iterations={self.iterations}"

        self.log(
            f"=== TCP CLIENT PRE-COPY MIGRATION ({self.iterations} iterations) ==="
        )

        self.log("Step 1: Checking source client process...")
        pid = self._get_pid()
        if not pid:
            self.log("ERROR: Could not read PID")
            self.metrics.notes += "; pid_not_found"
            return False

        endpoint = self._read_server_endpoint()
        vip = self._read_vip()
        if not endpoint or not vip:
            self.log(
                "ERROR: Missing endpoint/vip files on source (even after recreation attempt)"
            )
            self.metrics.notes += "; missing_endpoint_or_vip"
            return False

        before_conn = self._server_conn_count()
        if before_conn is not None:
            self.log(f"  Server connections so far: {before_conn}")

        self.metrics.src_arch = self.source.get_arch()
        self.metrics.dst_arch = self.dest.get_arch()
        self.metrics.same_arch = self.metrics.src_arch == self.metrics.dst_arch

        # Step 2: Prepare dirs
        self.log("Step 2: Preparing CRIU directory...")
        self.source.exec(
            "sudo rm -rf /tmp/CRIU-tcp-client && sudo mkdir -p /tmp/CRIU-tcp-client",
            check=False,
        )

        # Step 3: Pre-dumps while running
        self.log(
            f"Step 3: Running {self.iterations} pre-dumps (client keeps running)..."
        )
        t_predump_start = time.time_ns()
        for i in range(self.iterations):
            iter_dir = f"/tmp/CRIU-tcp-client/iter-{i}"
            prev_opt = ""
            if i > 0:
                prev_opt = f" --prev-images-dir /tmp/CRIU-tcp-client/iter-{i-1}"
            cmd = (
                f"sudo mkdir -p {iter_dir} && "
                f"sudo criu pre-dump -t {pid} -D {iter_dir}{prev_opt} "
                "--shell-job --skip-file-rwx-check --track-mem --tcp-established -v4"
            )
            rc, _, _ = self.source.exec(cmd, check=False)
            if rc != 0:
                self.log(f"  WARNING: pre-dump {i+1}/{self.iterations} failed")
                self.metrics.notes += f"; predump_failed_{i+1}"
            else:
                self.log(f"  Pre-dump {i+1}/{self.iterations} complete")
            time.sleep(1)
        t_predump_done = time.time_ns()
        self.metrics.predump_ms = int((t_predump_done - t_predump_start) // 1_000_000)

        # Step 4: Final dump (freeze)
        self.log("Step 4: Final dump (freezing client)...")
        prev_opt = ""
        if self.iterations > 0:
            prev_opt = (
                f" --prev-images-dir /tmp/CRIU-tcp-client/iter-{self.iterations - 1}"
            )
        t_final_dump_start = time.time_ns()
        rc, _, _ = self.source.exec(
            f"sudo criu dump -t {pid} -D /tmp/CRIU-tcp-client -v4 -o dump.log{prev_opt} "
            "--shell-job --skip-file-rwx-check --tcp-established",
            check=False,
        )
        t_final_dump_done = time.time_ns()
        self.metrics.final_dump_ms = int(
            (t_final_dump_done - t_final_dump_start) // 1_000_000
        )
        self.metrics.checkpoint_ms = self.metrics.final_dump_ms
        if rc != 0:
            self.log("ERROR: Final dump failed")
            _, dump_log, _ = self.source.exec(
                "sudo tail -n 120 /tmp/CRIU-tcp-client/dump.log", check=False
            )
            if dump_log:
                self.log(f"Dump log:\n{dump_log}")
            self.metrics.notes += "; dump_failed"
            return False

        # Move VIP now that the client is frozen
        self.log("Step 4.5: Moving VIP to destination...")
        if not self._move_vip(vip=vip, server_ip=endpoint.ip):
            return False

        # Step 5: Archive
        self.log("Step 5: Creating archive...")
        self.source.exec(
            "sudo tar -C /tmp -czf /tmp/CRIU-tcp-client.tar.gz CRIU-tcp-client && "
            "sudo cp /tmp/CRIU-tcp-client.tar.gz /home/ubuntu/CRIU-tcp-client.tar.gz && "
            "sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-tcp-client.tar.gz",
            check=False,
        )
        _, size_str, _ = self.source.exec(
            "sudo stat -c %s /tmp/CRIU-tcp-client.tar.gz", check=False
        )
        self.metrics.archive_bytes = (
            int(size_str) if (size_str or "").strip().isdigit() else 0
        )

        # Step 6: Transfer
        self.log("Step 6: Transferring archive...")
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
                "/home/ubuntu/CRIU-tcp-client.tar.gz",
                "/home/ubuntu/CRIU-tcp-client.tar.gz",
                relay_node=self.relay_node,
            )
            if self.transfer_mode == "host"
            else transfer_archive_direct(
                self.source.node,
                self.dest.node,
                "/home/ubuntu/CRIU-tcp-client.tar.gz",
                "/home/ubuntu/CRIU-tcp-client.tar.gz",
            )
        )
        t_transfer_done = time.time_ns()
        self.metrics.transfer_ms = int(
            (t_transfer_done - t_transfer_start) // 1_000_000
        )
        if not transfer_ok:
            self.log("ERROR: Transfer failed")
            self.metrics.notes += "; transfer_failed"
            return False

        # Step 7: Unpack on destination
        self.log("Step 7: Unpacking on destination...")
        rc, _, _ = self.dest.exec(
            "sudo rm -rf /tmp/CRIU-tcp-client && sudo mkdir -p /tmp/CRIU-tcp-client && "
            "sudo tar -C /tmp -xzf /home/ubuntu/CRIU-tcp-client.tar.gz",
            check=False,
        )
        if rc != 0:
            self.log("ERROR: Unpack failed")
            self.metrics.notes += "; unpack_failed"
            return False

        # Step 8: Prepare restore
        self.log("Step 8: Preparing restore environment...")
        self.dest.exec(
            "touch /home/ubuntu/tcp_client.out && chmod 664 /home/ubuntu/tcp_client.out",
            check=False,
        )
        self._ensure_tcp_binary_on_dest()

        # Step 9: Restore
        self.log("Step 9: Restoring client (--tcp-established)...")
        t_restore_start = time.time_ns()
        rc, _, _ = self.dest.exec(
            "sudo criu restore -D /tmp/CRIU-tcp-client -v4 -o restore.log "
            "--shell-job --restore-detached --pidfile /tmp/CRIU-tcp-client/restored.pid "
            "--skip-file-rwx-check --tcp-established",
            check=False,
        )
        t_restore_done = time.time_ns()
        self.metrics.restore_ms = int((t_restore_done - t_restore_start) // 1_000_000)
        if rc != 0:
            self.log("ERROR: Restore failed")
            _, restore_log, _ = self.dest.exec(
                "sudo tail -n 120 /tmp/CRIU-tcp-client/restore.log", check=False
            )
            if restore_log:
                self.log(f"Restore log:\n{restore_log}")
            self.metrics.notes += "; restore_failed"
            self.metrics.success = False
            return False

        self.persist_restored_pid_files("/tmp/CRIU-tcp-client/restored.pid")

        # Verify
        self.log("Step 10: Verifying connection continuity...")
        time.sleep(3)
        _, last_line, _ = self.dest.exec(
            "tail -n 1 /home/ubuntu/tcp_client.out 2>/dev/null || true", check=False
        )
        self.log(f"  Client output (tail): {last_line}")
        rc_ss, ss_out, _ = self.dest.exec(
            f"ss -tn | grep -q ':{endpoint.port} ' && echo OK || echo NO",
            check=False,
        )
        ok_sock = "OK" in (ss_out or "")
        after_conn = self._server_conn_count()
        if after_conn is not None:
            self.log(f"  Server connections so far: {after_conn}")

        if (
            before_conn is not None
            and after_conn is not None
            and after_conn != before_conn
        ):
            self.log("✗ FAILED: Server accepted a new connection after restore")
            self.metrics.notes += f"; server_new_conn: {before_conn}->{after_conn}"
            self.metrics.success = False
        elif not ok_sock:
            self.log("✗ FAILED: No established TCP socket on destination")
            self.metrics.notes += "; tcp_not_established"
            self.metrics.success = False
        else:
            self.log("✓ SUCCESS: Client restored and TCP socket appears established")
            self.metrics.success = True

        self.metrics.downtime_ms = (
            self.metrics.final_dump_ms
            + self.metrics.transfer_ms
            + self.metrics.restore_ms
        )
        if self.metrics.transfer_ms > 0:
            self.metrics.bandwidth_mbps = (self.metrics.archive_bytes * 8) / (
                self.metrics.transfer_ms * 1000
            )
        return self.metrics.success


class TcpClientPostcopyMigration(TcpClientMigrationBase):
    def __init__(
        self,
        source: MultipassCommand,
        dest: MultipassCommand,
        *,
        server: MultipassCommand | None = None,
        transfer_mode: str = "host",
        relay_node: str | None = None,
        page_server_port: int = 9999,
    ):
        super().__init__(
            source,
            dest,
            server=server,
            transfer_mode=transfer_mode,
            relay_node=relay_node,
        )
        self.page_server_port = page_server_port

    def get_method_name(self) -> str:
        return "postcopy"

    def migrate(self, run_id: str) -> bool:
        # This follows the same structure as the memory-only post-copy, but adds:
        # - --tcp-established on dump/restore
        # - VIP move before restore
        self.metrics.run_id = run_id
        self.metrics.migration_method = "postcopy"
        self.metrics.technology = "CRIU"
        self.metrics.notes += f";lazy_pages_port={self.page_server_port}"

        self.log("=== TCP CLIENT POST-COPY (LAZY-PAGES) MIGRATION ===")

        self.log("Step 1: Checking source client process...")
        pid = self._get_pid()
        if not pid:
            self.log("ERROR: Could not read PID")
            self.metrics.notes += "; pid_not_found"
            return False

        endpoint = self._read_server_endpoint()
        vip = self._read_vip()

        if not endpoint or not vip:
            self.log(
                "ERROR: Missing endpoint/vip files on source (even after recreation attempt)"
            )
            self.metrics.notes += "; missing_endpoint_or_vip"
            return False

        before_conn = self._server_conn_count()
        if before_conn is not None:
            self.log(f"  Server connections so far: {before_conn}")

        self.metrics.src_arch = self.source.get_arch()
        self.metrics.dst_arch = self.dest.get_arch()
        self.metrics.same_arch = self.metrics.src_arch == self.metrics.dst_arch

        # Determine source IP for page-server connectivity
        source_ip = get_node_ip(self.source.node)
        if not source_ip:
            self.log("ERROR: Could not determine source node IP for lazy-pages")
            self.metrics.notes += "; source_ip_missing"
            return False

        port = int(self.page_server_port)
        self.log(f"  Page-server endpoint: {source_ip}:{port}")

        page_server_pid: str | None = None
        lazy_pages_pid: str | None = None

        try:
            # Step 2: Dump with --lazy-pages (background)
            self.log("Step 2: Final dump (freezing client) with --lazy-pages...")
            t_dump_start = time.time_ns()
            bind_addr = "0.0.0.0"
            dump_start_cmd = (
                "sudo rm -rf /tmp/CRIU-tcp-client && "
                "sudo mkdir -p /tmp/CRIU-tcp-client && "
                "sudo bash -lc '"
                "set -e; "
                "rm -f /tmp/CRIU-tcp-client/dump.pid /tmp/CRIU-tcp-client/dump.stdout; "
                "nohup criu dump -t "
                f"{pid}"
                " -D /tmp/CRIU-tcp-client -v4 -o dump.log "
                f"--shell-job --skip-file-rwx-check --lazy-pages --address {bind_addr} --port {port} "
                "--tcp-established "
                ">/tmp/CRIU-tcp-client/dump.stdout 2>&1 "
                "& echo $! > /tmp/CRIU-tcp-client/dump.pid; "
                "sleep 0.1; "
                "cat /tmp/CRIU-tcp-client/dump.pid'"
            )
            rc, dump_pid_str, _ = self.source.exec(dump_start_cmd, check=False)
            if rc != 0 or not dump_pid_str.strip().isdigit():
                self.log("ERROR: Failed to start lazy-pages dump on source")
                _, dump_log, _ = self.source.exec(
                    "sudo tail -n 120 /tmp/CRIU-tcp-client/dump.log", check=False
                )
                if dump_log:
                    self.log(f"Dump log:\n{dump_log}")
                self.metrics.notes += "; dump_start_failed"
                return False
            page_server_pid = dump_pid_str.strip()
            self.log(f"  Dump PID: {page_server_pid}")

            # Wait for inventory.img as readiness signal
            ready = False
            for _ in range(60):
                rc_inv, _, _ = self.source.exec(
                    "sudo test -f /tmp/CRIU-tcp-client/inventory.img", check=False
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
                self.metrics.notes += "; dump_ready_timeout"
            self.log(f"  Final dump time (freeze duration): {dump_ms} ms")

            # Move VIP now that the client is frozen/killed by dump
            self.log("Step 2.5: Moving VIP to destination...")
            if not self._move_vip(vip=vip, server_ip=endpoint.ip):
                return False

            # Step 3: Archive
            self.log("Step 3: Creating archive...")
            rc, _, _ = self.source.exec(
                "sudo tar -C /tmp -czf /tmp/CRIU-tcp-client.tar.gz CRIU-tcp-client && "
                "sudo cp /tmp/CRIU-tcp-client.tar.gz /home/ubuntu/CRIU-tcp-client.tar.gz && "
                "sudo chown ubuntu:ubuntu /home/ubuntu/CRIU-tcp-client.tar.gz",
                check=False,
            )
            if rc != 0:
                self.log("ERROR: Archive creation failed")
                self.metrics.notes += "; archive_failed"
                return False
            _, size_str, _ = self.source.exec(
                "sudo stat -c %s /tmp/CRIU-tcp-client.tar.gz", check=False
            )
            self.metrics.archive_bytes = (
                int(size_str) if (size_str or "").strip().isdigit() else 0
            )

            # Step 4: Transfer
            self.log("Step 4: Transferring archive...")
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
                    "/home/ubuntu/CRIU-tcp-client.tar.gz",
                    "/home/ubuntu/CRIU-tcp-client.tar.gz",
                    relay_node=self.relay_node,
                )
                if self.transfer_mode == "host"
                else transfer_archive_direct(
                    self.source.node,
                    self.dest.node,
                    "/home/ubuntu/CRIU-tcp-client.tar.gz",
                    "/home/ubuntu/CRIU-tcp-client.tar.gz",
                )
            )
            t_transfer_done = time.time_ns()
            self.metrics.transfer_ms = int(
                (t_transfer_done - t_transfer_start) // 1_000_000
            )
            if not transfer_ok:
                self.log("ERROR: Transfer failed")
                self.metrics.notes += "; transfer_failed"
                return False

            # Step 5: Unpack
            self.log("Step 5: Unpacking on destination...")
            rc, _, _ = self.dest.exec(
                "sudo rm -rf /tmp/CRIU-tcp-client && sudo mkdir -p /tmp/CRIU-tcp-client && "
                "sudo tar -C /tmp -xzf /home/ubuntu/CRIU-tcp-client.tar.gz",
                check=False,
            )
            if rc != 0:
                self.log("ERROR: Unpack failed")
                self.metrics.notes += "; unpack_failed"
                return False

            # Prepare restore environment
            self.dest.exec(
                "touch /home/ubuntu/tcp_client.out && chmod 664 /home/ubuntu/tcp_client.out",
                check=False,
            )
            self._ensure_tcp_binary_on_dest()

            # Step 6: Start lazy-pages daemon (fully detached)
            self.log("Step 6: Starting lazy-pages daemon on destination...")
            start_lazy_cmd = (
                "sudo bash -lc '"
                "set -e; "
                "cd /tmp/CRIU-tcp-client; "
                "rm -f /tmp/CRIU-tcp-client/lazy-pages.pid /tmp/CRIU-tcp-client/lazy-pages.stdout; "
                "nohup criu lazy-pages -D /tmp/CRIU-tcp-client "
                f"--page-server --address {source_ip} --port {port} "
                "-v4 -o /tmp/CRIU-tcp-client/lazy-pages.log "
                ">/tmp/CRIU-tcp-client/lazy-pages.stdout 2>&1 "
                "& echo $! > /tmp/CRIU-tcp-client/lazy-pages.pid; "
                "sleep 0.1; "
                "cat /tmp/CRIU-tcp-client/lazy-pages.pid'"
            )
            rc, lp_pid_str, _ = self.dest.exec(start_lazy_cmd, check=False)
            if rc != 0 or not lp_pid_str.strip().isdigit():
                self.log("ERROR: Failed to start lazy-pages daemon")
                _, lp_log, _ = self.dest.exec(
                    "sudo tail -n 120 /tmp/CRIU-tcp-client/lazy-pages.log", check=False
                )
                if lp_log:
                    self.log(f"Lazy-pages log:\n{lp_log}")
                self.metrics.notes += "; lazy_pages_start_failed"
                return False
            lazy_pages_pid = lp_pid_str.strip()

            for _ in range(50):
                rc_sock, _, _ = self.dest.exec(
                    "test -S /tmp/CRIU-tcp-client/lazy-pages.socket", check=False
                )
                if rc_sock == 0:
                    break
                time.sleep(0.1)

            # Step 7: Restore with --lazy-pages and --tcp-established
            self.log("Step 7: Restoring client...")
            t_restore_start = time.time_ns()
            restore_cmd = (
                "cd /tmp/CRIU-tcp-client && "
                "sudo criu restore -D /tmp/CRIU-tcp-client -v4 -o restore.log "
                "--shell-job --restore-detached --pidfile /tmp/CRIU-tcp-client/restored.pid "
                "--skip-file-rwx-check --lazy-pages --tcp-established"
            )
            rc, _, _ = self.dest.exec(restore_cmd, check=False)
            t_restore_done = time.time_ns()
            self.metrics.restore_ms = int(
                (t_restore_done - t_restore_start) // 1_000_000
            )
            if rc != 0:
                self.log("ERROR: Restore failed")
                _, restore_log, _ = self.dest.exec(
                    "sudo tail -n 120 /tmp/CRIU-tcp-client/restore.log", check=False
                )
                if restore_log:
                    self.log(f"Restore log:\n{restore_log}")
                self.metrics.notes += "; restore_failed"
                self.metrics.success = False
                return False

            self.persist_restored_pid_files("/tmp/CRIU-tcp-client/restored.pid")

            # Verify
            self.log("Step 8: Verifying connection continuity...")
            time.sleep(3)
            _, last_line, _ = self.dest.exec(
                "tail -n 1 /home/ubuntu/tcp_client.out 2>/dev/null || true", check=False
            )
            self.log(f"  Client output (tail): {last_line}")
            rc_ss, ss_out, _ = self.dest.exec(
                f"ss -tn | grep -q ':{endpoint.port} ' && echo OK || echo NO",
                check=False,
            )
            ok_sock = "OK" in (ss_out or "")
            after_conn = self._server_conn_count()
            if after_conn is not None:
                self.log(f"  Server connections so far: {after_conn}")

            if (
                before_conn is not None
                and after_conn is not None
                and after_conn != before_conn
            ):
                self.log("✗ FAILED: Server accepted a new connection after restore")
                self.metrics.notes += f"; server_new_conn: {before_conn}->{after_conn}"
                self.metrics.success = False
            elif not ok_sock:
                self.log("✗ FAILED: No established TCP socket on destination")
                self.metrics.notes += "; tcp_not_established"
                self.metrics.success = False
            else:
                self.log(
                    "✓ SUCCESS: Client restored and TCP socket appears established"
                )
                self.metrics.success = True

            self.metrics.downtime_ms = (
                self.metrics.final_dump_ms
                + self.metrics.transfer_ms
                + self.metrics.restore_ms
            )
            if self.metrics.transfer_ms > 0:
                self.metrics.bandwidth_mbps = (self.metrics.archive_bytes * 8) / (
                    self.metrics.transfer_ms * 1000
                )
            return self.metrics.success
        finally:
            # Cleanup: stop lazy-pages and page-server processes (best-effort)
            if lazy_pages_pid:
                self.dest.exec(
                    f"sudo kill -9 {lazy_pages_pid} 2>/dev/null || true", check=False
                )
            if page_server_pid:
                self.source.exec(
                    f"sudo kill -9 {page_server_pid} 2>/dev/null || true", check=False
                )
