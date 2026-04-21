tstamp -- whether timestamps on packets are supported

# CRIU, TCP Migration, and the Need for a Virtual IP (VIP)

## Why Can't We Just Change the IP?

When live-migrating a process with established TCP connections to a new host, a major challenge arises: **the destination host has a different IP address**. This is not just a CRIU limitation—it's fundamental to how TCP works.

**TCP connections are defined by a 4-tuple:**

> Source IP, Source Port, Destination IP, Destination Port

If you migrate a server to a new host with a different IP, the client will not recognize packets from the new IP. The connection will hang or time out, because the TCP protocol expects the same 4-tuple for the lifetime of the connection.

## How CRIU Handles Sockets

| Socket State           | Description                                 | CRIU Handling & Solutions                                                                 |
|----------------------- |---------------------------------------------|------------------------------------------------------------------------------------------|
| **Listening Sockets**  | Waiting for connections                     | If bound to `0.0.0.0` (INADDR_ANY), migration works. If bound to a specific IP, must edit image files. |
| **In-Flight Connections** | `connect()`-ed but not yet `accept()`-ed | Can be ignored during dump with `--skip-in-flight`.                                      |
| **Established Sockets**| Active connections with wired-in IPs        | Technically possible to restore with a new IP (by editing images), but the peer will reject packets. |

## Why a Virtual IP (VIP) is Needed

To achieve **seamless migration of established TCP connections**, you must ensure the client always sees the same server IP—even after migration. This is where a **Virtual IP (VIP)** comes in:

- The VIP is assigned to the server before migration.
- After migration, the VIP is moved (or re-routed) to the new host.
- The client continues to communicate with the same VIP, unaware of the backend migration.

**Without a VIP or similar proxy/relay, true transparent TCP migration is impossible.**

## What About Editing CRIU Images?

You can use [CRIT](https://criu.org/CRIT) to edit dumped image files (`inetsk.img`, `files.img`) and change the IP addresses. However, even if you restore the process with a new IP, the client will not accept packets from the new address. The connection will break or hang until TCP times out.

## Modern Solutions: Proxy, NAT, and VIP

The only robust solutions for live TCP migration are at the network layer:

- **Proxy/Relay (Recommended):** Place a proxy or load balancer in front of the service. The client connects to the proxy (VIP), which forwards traffic to the backend. After migration, the proxy updates its routing to point to the new backend.
- **NAT/IP Rewriting:** Use NAT to rewrite outgoing packets so they appear to come from the old IP. This is complex and fragile compared to a VIP/proxy.

## Summary Table: Socket Migration with CRIU

| Socket State           | Migration Feasible? | Notes                                                                 |
|----------------------- |--------------------|-----------------------------------------------------------------------|
| Listening (INADDR_ANY) | Yes                | No IP mismatch; works automatically                                   |
| Listening (specific IP)| Yes (with edit)    | Must edit image files to update IP                                    |
| In-Flight Connections  | Yes (skip)         | Use `--skip-in-flight`                                                |
| Established            | No (w/o VIP/proxy) | Peer will reject packets from new IP; use VIP/proxy for transparency  |

## Further Reading

- [CRIU: Change IP address](https://criu.org/Change_IP_address)
- [CRIU: TCP connection](https://criu.org/TCP_connection)
- [GitHub Issue: Live migration and IP change](https://github.com/checkpoint-restore/criu/issues/211)
