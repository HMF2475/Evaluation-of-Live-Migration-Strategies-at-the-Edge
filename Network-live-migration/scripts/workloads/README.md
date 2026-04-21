# Workloads

TCP workload launchers used by the orchestrators.

## Scripts

- `start_tcp_server.sh`: starts the TCP server on a fixed node (default `edge-host-1`).
- `start_tcp_client.sh`: starts the TCP client on source and binds it to `TCP_VIP`.

## Notes

- VIP binding is mandatory for cross-node restore of established TCP sockets.
- Client state files:
	- `/home/ubuntu/tcp_client.pid` (plus legacy `/home/ubuntu/client.pid`)
	- `/home/ubuntu/tcp_client.out`
	- `/home/ubuntu/tcp_vip.txt`
	- `/home/ubuntu/tcp_server_endpoint.txt`
- Server state files:
	- `/home/ubuntu/tcp_server.pid`
	- `/home/ubuntu/tcp_server.out`

See `Network-live-migration/TCP-live-migration.md` for complete run commands.
