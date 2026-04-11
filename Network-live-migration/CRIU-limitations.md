This is copied from https://criu.org/Change_IP_address:
Change IP address
Jump to navigationJump to search
When doing a live migration of a process from one host to another a common question is -- how to deal with the different IP address on the destination host. Although the correct answer would be to use containers, moving a service onto different IP address might make sense. This article describes how to do it.

Note.svg	Note: This is not yet implemented in CRIU: [1]

Contents
1	Problem
1.1	Listening sockets
1.2	In-flight connections
1.3	Established sockets
2	Possible solution
Problem
Just changing the IP address and letting the things work as they used to is not possible not due to CRIU constraints, but due to how TCP connection operates according to the protocol. One cannot proceed the packet flow with one IP address changed, the client would just ignore such packets.

So when talking about migrating a server to some other place with some other IP three things are to be considered.

Listening sockets
If your server is bound to 0.0.0.0 (INADDR_ANY) then migration would "just work" there's no IP address that would mismatch. If your server is bound to some device, then you'll have to change the binding IP address. Right now this can be done by editing the images, in particular, all PF_INET sockets sit in files.img image and CRIT can be used to modify one.

In-flight connections
These are connect()-ed, but not yet accept()-ed. We have an option --skip-in-flight that makes criu ignore these guys.

Established sockets
These guys are tough, as they do have some real IP address wired into their configuration. Technically it's possible to restore the socket with different IP address (by modifying the inetsk.img with CRIT), but as was said -- the peer would not accept that. In the worst case the connection would get stuck till TCP timeout.

Possible solution
So if we're OK with just breaking these connections we need to teach criu to break them. There are two things to consider while doing this.

a) Dumping sockets. Since we don't really need the connection we'd need to teach criu to skip those guys. The code dumping PF_INET sockets is in criu/sk-inet.c, the code dumping IPPROTO_TCP stuff is in criu/sk-tcp.c

b) Restoring sockets. Just leaving the hole in the place where the connected socket was is not nice, the server would get wrong error codes from syscalls and, which is worse, the hole might become busy with some other file (when server does open/socket/accept/whatever) which will break server internal logic. So at restore time we'd need to put some stub into the descriptor. I would suggest addressing this dump-time and instead of dumping the established socket into image dump the socket that looks like closed one. In this case socket restoring code would just restore the closed socket into proper place.


this is copied from https://criu.org/TCP_connection:
TCP connection
Jump to navigationJump to search
This page describes how we handle established TCP connections.


Contents
1	TCP repair mode in kernel
1.1	Sequences
1.2	Packets in queue
1.3	Options
2	Timestamp
3	Checkpoint and restore TCP connection
4	States
4.1	TCP_SYN_SENT
4.2	Half-closed sockets
5	See also
6	External links
TCP repair mode in kernel
The TCP_REPAIR socket option was added to the kernel 3.5 to help with C/R for TCP sockets.

When this option is used, the socket is switched into a special mode, in which any action performed on it does not result in anything defined by an appropriate protocol actions, but rather directly puts the socket into the state that the socket is expected to be in at the end of a successfully finished operation.

For example, calling connect() on a repaired socket just changes its state to ESTABLISHED, with the peer address set as requested. The bind() call forcibly binds the socket to a given address (ignoring any potential conflicts). The close() call closes the socket without any transient FIN_WAIT/TIME_WAIT/etc states, socket is silently killed.

Sequences
To restore the connection properly, bind() and connect() is not enough. One also needs to restore the TCP sequence numbers. To do so, the TCP_REPAIR_QUEUE and TCP_QUEUE_SEQ options were introduced.

The former one selects which queue (input or output) will be repaired and the latter gets/sets the sequence. Note setting the sequence is only possible on CLOSE-d socket.

Packets in queue
When set the queue to repair as described above, one can call recv or send syscalls on a repaired socket. Both calls result on peeking or poking data from/to the respective queue. This sounds funny, but yes, for repaired socket one can receve the outgoing and send the incoming queues. Using the MSG_PEEK flag for recv() is required.

Options
There are 4 options that are negotiated by the socket at the connecting stage. These are

mss_clamp -- the maximum size of the segment peer is ready to accept
snd _scale -- the scale factor for a window
sack -- whether selective acks are permitted or not
tstamp -- whether timestamps on packets are supported
All four can be read with getsockopt() calls to a socket and in order to restore them the TCP_REPAIR_OPTIONS sockoption is introduced.

Timestamp
"The sender's timestamp clock is used as a source of monotonic non-decreasing values to stamp the segments"(rfc7323). The Linux kernel uses the jiffies counter as the tcp timestamp.

#define tcp_time_stamp ((__u32)(jiffies))

We add the TCP_TIMESTAMP options to be able to compensate a difference between jiffies counters, when a connection is migrated on another host. When a connection is dumped, criu calls getsockopt(TCP_TIMESTAMP) to get a current timestamp, then on restore it calls setsockopt(TCP_TIMESTAMP) to set this timestamp as a starting point.

Checkpoint and restore TCP connection
With the above sockoptions dumping and restoring TCP connection becomes possible. The criu just reads the socket state and restores it back letting the protocol resurrect the data sequence.

One thing to note here — while the socket is closed between dump and restore the connection should be "locked", i.e. no packets from peer should enter the stack, otherwise the RST will be sent by a kernel. In order to do so a simple netfilter rule is configured that drops all the packets from peer to a socket we're dealing with. This rule sits in the host netfilter tables after the criu dump command finishes and it should be there when you issue the criu restore one. The locking method can be specified using the --network-lock option.

Another thing to note is -- on restore there should be available the IP address, that was used by the connection. This is automatically so if restore happens on the same box as dump. In case of hand-made live migration the IP address should be copied too.

That said, the command line option --tcp-established should be used when calling criu to explicitly state, that the caller is aware of this "transitional" state of the netfilter.

In case the target process lives in NET namespace the connection locking happens the other way. Instead of per-connection iptables rules the "network-lock"/"network-unlock" action scripts are called so that the user could isolate the whole netns from network. Typically this is done by downing the respective veth pair end.

States
TCP_SYN_SENT
There is only one difference with TCP_ESTABLISHED, we have to restore a socket and disable the repair mode before calling connect(). The kernel will send a one syn-sent packet with the same initial sequence number and sets the TCP_SYN_SENT state for the socket.

Half-closed sockets
A socket is half-closed when it sent or received a fin packet. These sockets are in one for these states: TCP_FIN_WAIT1, TCP_FIN_WAIT2, TCP_CLOSING, TCP_LAST_ACL, TCP_CLOSE_WAIT. To restore these states, we restore a socket into the TCP_ESTABLISHED state and then we call shutfown(SHUT_WR), if a socket has sent a fin packet and we send a fake fin packet, if a socket has received it before. For example, if we want to restore the TCP_FIN_WAIT1 state, we have to call shutfown(SHUT_WR) and we can send a fake ack to the fin packet to restore the TCP_FIN_WAIT2 state.

See also
Simple TCP pair
TCP repair TODO
Dropping the connection


This is summarized from the issue https://github.com/checkpoint-restore/criu/issues/211: 
Here is a comprehensive summary of the GitHub discussion regarding live migrating TCP connections and changing IP addresses using CRIU (Checkpoint/Restore In Userspace).

### The Core Challenge: TCP Protocol Constraints
The primary takeaway from the discussion is that **transparently changing an IP address during a live migration is a limitation of the TCP protocol, not CRIU.** TCP connections are strictly defined by a 4-tuple (Source IP, Source Port, Destination IP, Destination Port). If a server process is migrated to a new host with a different IP address, the client (peer) will not recognize packets coming from the new IP because they do not match the established 4-tuple. The client will ignore the packets, and the connection will ultimately time out.

---

### How CRIU Handles Socket States
When attempting to migrate connections, you must account for the different states a socket might be in. Here is how CRIU approaches them:

| Socket State | Description | CRIU Handling & Solutions |
| :--- | :--- | :--- |
| **Listening Sockets** | Waiting for connections. | If bound to `0.0.0.0` (INADDR_ANY), migration works automatically. If bound to a specific IP, the IP must be manually changed in the image files. |
| **In-Flight Connections** | `connect()`-ed but not yet `accept()`-ed. | Can be safely ignored during the dump by using the `--skip-in-flight` command-line option. |
| **Established Sockets** | Active connections with wired-in IPs. | The hardest to migrate. CRIU can technically restore these with a new IP by modifying the image files, but the remote peer will reject the packets. |

---

### How to Modify CRIU Images for Migration
If you need to change the IP address of a bound socket or attempt to manually force an IP change, you must use **CRIT** (the CRIU Image Tool) to edit the dumped image files before restoring.

**Steps discussed to alter the IP:**
1.  **Dump the process** using standard CRIU commands.
2.  **Edit `inetsk.img`**: This file contains all the `PF_INET` sockets. You must update the old IP address to the new IP address here.
3.  **Edit `files.img`**: You must also locate the socket entries in this file and update the `src_addr` field to match the new IP.
4.  **Restore the process** using the modified images. 

*Note: While this allows CRIU to successfully restore the process on the new machine without throwing an error about the old IP, the remote client will still drop the traffic unless a network-level workaround is in place.*

### Gracefully Breaking Connections
Because maintaining the exact TCP connection natively is highly problematic, the CRIU maintainers suggest that if you must drop the connection, it should be done gracefully so the server process doesn't break:
* **Dumping:** Modify CRIU (`criu/sk-inet.c` and `criu/sk-tcp.c`) to dump established sockets as if they were *closed* sockets.
* **Restoring:** Upon restore, the server will see a closed connection rather than an empty file descriptor hole. This prevents the server from assigning a new, unrelated file to that descriptor, which would break internal server logic.

---

### Workarounds and Modern Solutions
Because CRIU can only control the server side of the connection, the community concluded that attempting to force the client to accept a new IP via CRIU (like sending "magic" update packets) introduces severe security risks like IP spoofing. 

To achieve true connection continuity, the solution lies outside of CRIU at the network layer:

* **NAT / IP Rewriting:** You can use CRIT to update the IPs locally, and then set up a NAT on the new host to rewrite outgoing packets so they appear to come from the old IP. 
* **External Proxy Layers (Recommended):** The most viable and widely accepted solution is to rely on external network mechanisms rather than CRIU's internal TCP handling. 

By placing a proxy layer, Load Balancer, or Virtual IP in front of the server, the client maintains a steady connection to the proxy. When CRIU live-migrates the backend container to a new host, the proxy simply updates its routing tables to forward traffic to the new backend IP, keeping the migration entirely transparent to the end-user.
