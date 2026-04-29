# CRIU, TCP Migration, and the Need for a VIP

This repository migrates a **TCP client process** from `edge-node-1` to `edge-node-2` while it keeps an established connection to a fixed server on `edge-host-1`.

## TCP Constraint

An established TCP connection is identified by a 4-tuple:

> client IP, client port, server IP, server port

If the client process is restored on another host with a different local IP, the server sees packets from a different peer. The restored socket state no longer matches the connection the server knows, so continuity fails.

CRIU can dump/restore established TCP sockets with TCP repair mode, but it cannot make the peer accept a changed 4-tuple.

## Repo Solution: Client VIP

This workflow preserves the client-side IP with a virtual IP:

- Client starts on `edge-node-1` and binds to VIP `10.22.132.250`.
- Server remains on its normal `edge-host-1` IP.
- During migration, orchestrator moves VIP from source client node to destination client node.
- Destination emits gratuitous ARP so the server relearns VIP -> destination MAC.
- Restored client keeps the same local IP and socket tuple.

So, in this repo, VIP belongs to the **migrated TCP client**, not the server.

## Why Not Just Edit CRIU Images?

CRIT can edit dumped image files such as `inetsk.img`, but changing socket IPs changes the TCP tuple. The peer still expects the old tuple. Editing images alone also does not update routing/ARP state in the network.

For transparent established-socket migration, preserve the address the peer already knows or put a stable proxy/NAT layer in front.

## General Cases

| Migrated process | Address that must stay stable | Typical solution |
|------------------|-------------------------------|------------------|
| TCP client | Client local IP | Move client VIP source -> destination |
| TCP server | Server service IP | Move server VIP, use proxy/LB, or NAT |
| Listening socket bound to `0.0.0.0` | No specific bind IP | Usually easier |
| Listening socket bound to specific IP | Bound IP | Preserve/move that IP or edit images carefully |
| In-flight, not-yet-accepted connection | Pending state | `--skip-in-flight` may be usable |

## Repo Checks

Before migration:

```bash
multipass exec edge-node-1 -- ip -4 addr | grep 10.22.132.250
multipass exec edge-node-1 -- ss -tn state established
```

After migration:

```bash
multipass exec edge-node-2 -- ip -4 addr | grep 10.22.132.250
multipass exec edge-node-1 -- ip -4 addr | grep 10.22.132.250 || true
multipass exec edge-host-1 -- ip neigh | grep 10.22.132.250 || true
```

## Further Reading

- [CRIU: Change IP address](https://criu.org/Change_IP_address)
- [CRIU: TCP connection](https://criu.org/TCP_connection)
- [GitHub Issue: Live migration and IP change](https://github.com/checkpoint-restore/criu/issues/211)
