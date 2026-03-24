# Live Container Migration — Tactical Edge Environment Demo

## What This Is

A **minimal, copy-paste demo** of container live migration between two Kubernetes nodes
using **Podman + CRIU** (Checkpoint/Restore In Userspace).

The migration path:

```
edge-node-1 (source)                    edge-node-2 (destination)
──────────────────────                  ──────────────────────────
Podman container running                podman container restore
  prints: 0  1  2  3  4  5 ...         ← resumes: 6  7  8  9 ...
podman container checkpoint
  saves full CRIU dump to tar.zst
multipass transfer (network link)
```

The counter value is **preserved across the node boundary** — proof that in-memory
process state survived the migration.

This experiment is a **memory-only migration baseline**. It validates preservation of
in-memory process state, but it does **not** preserve active network sockets, external
client connections, or persistent storage state.

---

## Context: Federated Tactical Clouds (NATO)

| Term | Meaning here |
|---|---|
| **TEE** | Tactical Edge Environment — small cloud infra on vehicles, edge devices |
| **Tactical Cloud** | An autonomous K8s cluster operated by a single coalition unit |
| **Federated Tactical Cloud** | Multiple tactical clouds sharing workloads while keeping autonomy |
| **Soldier A → Soldier B** | Migrating a service from Unit A's cluster to Unit B's cluster |
| **Migration** | Moving a running service without restarting from scratch |

Why migration matters:
- Low bandwidth, intermittent connectivity → services must move when links drop
- No global root → each cloud is autonomous but must accept migrated workloads
- CRIU preserves the **full in-memory state**: counters, sessions, ML model context — no cold restart

---

## Tools

| Tool | Role |
|---|---|
| **Terraform + Multipass** | Provision 2 local VMs simulating 2 tactical edge nodes |
| **Kubernetes v1.30** | Orchestration context (cluster of tactical cloud nodes) |
| **CRIU** | Low-level checkpoint/restore engine, built from source |
| **Podman** | Container runtime — `podman checkpoint` and `podman restore` |
| **checkpointctl** | Inspect checkpoint archives (installed on host) |

---

## Part 1 — Provision Nodes

### Prerequisites (host machine)

```bash
which multipass || sudo snap install multipass
which terraform  || sudo snap install terraform --classic
```

### Launch

```bash
cd tools/terraform
terraform init    # only needed once
terraform apply -auto-approve
```

After ~30 seconds both VMs appear:

```
Name          State    IPv4
edge-node-1   Running  10.22.x.x
edge-node-2   Running  10.22.x.x
```

> **Important:** All heavy installs (containerd, K8s tools, CRIU build) run in a
> **background systemd service** called `node-bootstrap`. Multipass returns immediately;
> the provisioning continues inside the VM.

Monitor bootstrap progress (required before any K8s or checkpoint step):

```bash
multipass exec edge-node-1 -- sudo journalctl -u node-bootstrap -f --no-pager
# Wait for the final line: "[bootstrap] Node fully provisioned."
# This takes about 15–20 minutes (CRIU compiles from source).
```

You can also watch a shorter progress summary:

```bash
multipass exec edge-node-1 -- sudo journalctl -u node-bootstrap --no-pager | \
  grep -E 'bootstrap\]|Error|error' | tail -20
```

---

## Part 2 — Build the Kubernetes Cluster

> Wait for `edge-node-1` bootstrap to complete before these steps.

### Phase A — Verify tools are ready (both nodes)

```bash
# Run on each node to confirm K8s tools are installed
for node in edge-node-1 edge-node-2; do
  echo "=== $node ==="
  multipass exec $node -- bash -c '
    echo "containerd: $(containerd --version)"
    echo "kubelet:    $(kubelet --version)"
    echo "kubeadm:    $(kubeadm version -o short)"
    echo "criu:       $(criu --version 2>&1 | head -1)"
  '
done
```

### Phase B — Initialize the control plane (edge-node-1 only)

```bash
multipass exec edge-node-1 -- sudo kubeadm init --pod-network-cidr=10.244.0.0/16
```

Wait for:
```
Your Kubernetes control-plane has initialized successfully!
```

Configure kubectl:

```bash
multipass exec edge-node-1 -- bash -c '
  mkdir -p $HOME/.kube
  sudo cp /etc/kubernetes/admin.conf $HOME/.kube/config
  sudo chown $(id -u):$(id -g) $HOME/.kube/config
'
```

Apply Flannel CNI:

```bash
multipass exec edge-node-1 -- kubectl apply -f \
  https://github.com/flannel-io/flannel/releases/download/v0.25.6/kube-flannel.yml
```

Why this is included:
- The guide uses a Kubernetes cluster baseline, and nodes typically remain `NotReady`
  until a CNI plugin is installed.
- Flannel provides that CNI so Kubernetes networking and pod lifecycle become healthy.
- The migration demo itself is Podman-native and memory-only (`--network=none`), but
  keeping the cluster healthy is important for consistency and for later K8s-based tests.

Verify control-plane is Ready (wait ~30 s):

```bash
multipass exec edge-node-1 -- kubectl get nodes -o wide
# NAME          STATUS   ROLES           VERSION
# edge-node-1   Ready    control-plane   v1.30.x
```

Get the join command:

```bash
multipass exec edge-node-1 -- kubeadm token create --print-join-command
# Copy the full output.
```

### Phase C — Join the worker (edge-node-2 only)

```bash
# Paste the exact output from the command above
multipass exec edge-node-2 -- sudo kubeadm join <NODE1_IP>:6443 \
  --token <token> \
  --discovery-token-ca-cert-hash sha256:<hash>
```

Verify (wait ~60 s for edge-node-2 to become Ready):

```bash
multipass exec edge-node-1 -- kubectl get nodes -o wide
# NAME          STATUS   ROLES           VERSION
# edge-node-1   Ready    control-plane   v1.30.x
# edge-node-2   Ready    <none>          v1.30.x
```

---

## Part 3 — Run the Counter Container on edge-node-1

We run this as a **Podman container** (root, required for CRIU checkpoint).
The container prints an incrementing integer every second — simple enough to
visually confirm state preservation after restore.

```bash
# On edge-node-1
multipass exec edge-node-1 -- sudo podman run -d \
  --name counter \
  --network=none \
  --security-opt apparmor=unconfined \
  busybox:latest \
  sh -c 'i=0; while true; do echo $i; i=$((i+1)); sleep 1; done'
```

> `--network=none` avoids network namespace complexity in CRIU;
> this is a memory-only migration (E1 scenario).
> `--security-opt apparmor=unconfined` avoids destination profile mismatch during
> CRIU restore on Ubuntu hosts where the generated `containers-default-*` profile
> name may differ across nodes.

Watch the counter live:

```bash
multipass exec edge-node-1 -- sudo podman logs -f counter
# 0
# 1
# 2
# 3   ← (Ctrl+C to stop watching; container keeps running)
```

Confirm its running:

```bash
multipass exec edge-node-1 -- sudo podman ps
```

### Optional: Automated Metrics Run (Script)

If you prefer an automated run, execute the script now (after Part 3).
It will perform checkpoint, transfer, restore, and write one CSV row.

Run from repository root:

```bash
bash Container/scripts/orchestrators/collect_podman_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter \
  --scenario E1_memory_only \
  --run-id e1-run-001 \
  --csv Container/metrics/migration_metrics.csv
```
After the script finishes, you can run:
```bash
multipass exec edge-node-2 -- sudo podman logs -f counter
# You're going to see the counter continue on edge-node-2 without interruption
```

Important:
- If you use this script, skip manual Part 4, Part 5, and Part 6 for that run.
- Use Part 7 only for extra visual verification.

---

## Part 4 — Checkpoint the Container

Let it count for at least 10 seconds, then checkpoint:

```bash
# Capture the latest counter value before checkpoint (for continuity check later)
LAST_BEFORE=$(multipass exec edge-node-1 -- sudo podman logs --tail=200 counter | \
  awk '/^[0-9]+$/{v=$1} END{print v}')
echo "Last value before checkpoint: ${LAST_BEFORE}"

# Record start time (on host)
T_CHECKPOINT_START=$(date +%s%N)

multipass exec edge-node-1 -- sudo podman container checkpoint \
  --export=/tmp/counter-checkpoint.tar.zst \
  counter

# Record end time
T_CHECKPOINT_DONE=$(date +%s%N)
CHECKPOINT_MS=$(( (T_CHECKPOINT_DONE - T_CHECKPOINT_START) / 1000000 ))
echo "Checkpoint time: ${CHECKPOINT_MS} ms"
```

The container is now **stopped** on edge-node-1 and its full process state is saved to
`/tmp/counter-checkpoint.tar.zst`.

### Inspect the checkpoint (optional, on host)

```bash
# On the HOST machine (checkpointctl is installed locally)
# The archive in /tmp is root-owned, so stage it as ubuntu first.
multipass exec edge-node-1 -- bash -c '
  sudo cp /tmp/counter-checkpoint.tar.zst /home/ubuntu/counter-checkpoint.tar.zst
  sudo chown ubuntu:ubuntu /home/ubuntu/counter-checkpoint.tar.zst
'

# Use a deterministic host path to avoid /tmp path confusion and overwrite prompts.
HOST_CKPT="$PWD/counter-checkpoint.tar.zst"
rm -f "$HOST_CKPT"
multipass transfer edge-node-1:/home/ubuntu/counter-checkpoint.tar.zst "$HOST_CKPT"

checkpointctl show "$HOST_CKPT"
checkpointctl inspect --ps-tree --metadata "$HOST_CKPT"
```

Expected output from `show`:
```
CONTAINER   IMAGE             RUNTIME   ENGINE   CHKPT SIZE
---------   -----             -------   ------   ----------
counter     busybox:latest    runc      Podman   ~100 KiB
```

The `inspect` tree shows the `sh` process and the last counter value in its memory — this
is exactly what travels to edge-node-2.

Archive size (bandwidth requirement):

```bash
ls -lh "$HOST_CKPT"
```

---

## Part 5 — Transfer to edge-node-2 (Simulated Tactical Link)

```bash
# Stage readable copy on edge-node-1
multipass exec edge-node-1 -- bash -c '
  sudo cp /tmp/counter-checkpoint.tar.zst /home/ubuntu/counter-checkpoint.tar.zst
  sudo chown ubuntu:ubuntu /home/ubuntu/counter-checkpoint.tar.zst
'

# Transfer: edge-node-1 → host → edge-node-2
T_TRANSFER_START=$(date +%s%N)

HOST_CKPT="$PWD/counter-checkpoint.tar.zst"
rm -f "$HOST_CKPT"

multipass transfer edge-node-1:/home/ubuntu/counter-checkpoint.tar.zst \
  "$HOST_CKPT"

multipass transfer "$HOST_CKPT" \
  edge-node-2:/home/ubuntu/counter-checkpoint.tar.zst

T_TRANSFER_DONE=$(date +%s%N)
TRANSFER_MS=$(( (T_TRANSFER_DONE - T_TRANSFER_START) / 1000000 ))
echo "Transfer time: ${TRANSFER_MS} ms"
echo "Archive size:  $(du -sh "$HOST_CKPT" | cut -f1)"
```

---

## Part 6 — Restore on edge-node-2

```bash
# Move archive to /tmp for restore
multipass exec edge-node-2 -- bash -c '
  sudo cp /home/ubuntu/counter-checkpoint.tar.zst /tmp/counter-checkpoint.tar.zst
'

T_RESTORE_START=$(date +%s%N)

multipass exec edge-node-2 -- sudo podman container restore \
  --import=/tmp/counter-checkpoint.tar.zst \
  --name counter

T_RESTORE_DONE=$(date +%s%N)
RESTORE_MS=$(( (T_RESTORE_DONE - T_RESTORE_START) / 1000000 ))
echo "Restore time: ${RESTORE_MS} ms"

# Capture the first observed value after restore and compare expected continuity
sleep 2
OBSERVED_AFTER=$(multipass exec edge-node-2 -- sudo podman logs --tail=1 counter | tr -d '\r')
EXPECTED_NEXT=$((LAST_BEFORE + 1))
echo "Expected next value: ${EXPECTED_NEXT}"
echo "Observed after restore: ${OBSERVED_AFTER}"
```

---

## Part 7 — Verify: Counter Continues

```bash
multipass exec edge-node-2 -- sudo podman logs --tail=20 counter
```

**Expected — the counter picks up where it left off:**

```
...
11       ← last value printed on edge-node-1
12       ← resumed on edge-node-2 from saved state
13
14
```

Confirm the container is running on the destination node:

```bash
multipass exec edge-node-2 -- sudo podman ps
# CONTAINER ID  IMAGE          COMMAND  CREATED  STATUS    NAMES
# ...           busybox:latest          ...      Running   counter
```

This is the proof: **process state was preserved across the node boundary** — the
counter did not restart from zero.

---

## Part 8 — Metrics Summary

Record these values for the CSV. These metrics correspond to the **memory-only**
container migration baseline used in this experiment:

| Metric | Command | label |
|---|---|---|
| Checkpoint time | `echo $CHECKPOINT_MS` ms | `checkpoint_ms` |
| Archive size | `stat -c %s "$HOST_CKPT"` | `archive_bytes` |
| Transfer time | `echo $TRANSFER_MS` ms | `transfer_ms` |
| Restore time | `echo $RESTORE_MS` ms | `restore_ms` |
| Total downtime | `checkpoint_ms + transfer_ms + restore_ms` | `downtime_ms` |

Append to `Container/metrics/migration_metrics.csv`:

```
run_id,scenario,checkpoint_ms,archive_bytes,transfer_ms,restore_ms,downtime_ms,src_arch,dst_arch,same_arch
e1-run-001,E1_memory_only,<val>,<val>,<val>,<val>,<val>,x86_64,x86_64,true
```

---

## Part 9 — Teardown

```bash
cd tools/terraform
terraform destroy -auto-approve
```

---

## Part 10 — Stop/Resume VMs (Without Destroy)

Use this when you want to pause the lab and continue later with the same VMs.

Stop both nodes:

```bash
multipass stop edge-node-1 edge-node-2
```

Start both nodes again:

```bash
multipass start edge-node-1 edge-node-2
```

After restart, wait for node bootstrap readiness checks to pass:

```bash
for n in edge-node-1 edge-node-2; do
  echo "=== $n ==="
  multipass exec $n -- bash -c '
    systemctl is-active node-bootstrap || true
    criu --version 2>/dev/null | head -1 || true
    sudo podman --version
  '
done
```

If Kubernetes was already initialized before stopping, re-check cluster state:

```bash
multipass exec edge-node-1 -- kubectl get nodes -o wide
```

---

## Part 11 — Reset Lab State (Run Migration Again)

Use this to keep the same VMs but remove migration artifacts and rerun from Part 3.

```bash
for n in edge-node-1 edge-node-2; do
  echo "=== reset $n ==="
  multipass exec $n -- bash -c '
    sudo podman rm -f counter 2>/dev/null || true
    sudo podman rm -fa 2>/dev/null || true
    sudo podman system reset -f
    sudo rm -f /tmp/counter-checkpoint.tar.zst /home/ubuntu/counter-checkpoint.tar.zst
  '
done

# Remove host-staged archive
rm -f "$PWD/counter-checkpoint.tar.zst"
```

After reset, restart from:
- Part 3 (run counter)
- Part 4 (checkpoint)
- Part 5 (transfer)
- Part 6 (restore)

---

## Troubleshooting

### `terraform apply` times out (multipass launch timeout)

The `runcmd` in `cloud-init.yaml` must complete in under ~5 minutes. The current version
moves all heavy work to the `node-bootstrap` background service. If you still time out:

```bash
# After launch fails, check what cloud-init was doing
multipass exec edge-node-1 -- sudo journalctl -u cloud-init --no-pager | tail -30
```

### Bootstrap not started / K8s tools missing after node is up

```bash
multipass exec edge-node-1 -- sudo systemctl status node-bootstrap
multipass exec edge-node-1 -- sudo journalctl -u node-bootstrap --no-pager | tail -30
```

If the service failed, start it manually:

```bash
multipass exec edge-node-1 -- sudo systemctl start node-bootstrap
```

### `criu: command not found` when checkpointing

Bootstrap is still running (CRIU build takes 10–15 minutes). Wait for it:

```bash
multipass exec edge-node-1 -- sudo journalctl -u node-bootstrap -f --no-pager
# Look for: "[bootstrap] Node fully provisioned."
```

### `podman container restore` fails: "container creation timeout"

Stale state from a previous interrupted restore. Clean up and retry:

```bash
multipass exec edge-node-2 -- bash -c '
  sudo podman rm -f counter 2>/dev/null || true
  sudo podman system reset -f
'
# Then retry Part 6.
```

### `podman container restore` hangs and container stays in `Created`

If logs show AppArmor errors like:
- `can't write lsm profile -2`
- `changeprofile containers-default-...`

the checkpoint was created with an AppArmor label that does not exist on the
destination node. Re-run Part 3 using `--security-opt apparmor=unconfined`, then
checkpoint/transfer/restore again.

Quick recovery on destination before retrying restore:

```bash
multipass exec edge-node-2 -- bash -c '
  sudo podman rm -f counter 2>/dev/null || true
  sudo podman system reset -f
'
```

### `kubeadm join` fails after a partial attempt

```bash
multipass exec edge-node-2 -- sudo kubeadm reset -f
# Re-generate join command on edge-node-1:
multipass exec edge-node-1 -- kubeadm token create --print-join-command
```

---

## Notes

- **Why Podman-native migration**: The Kubernetes kubelet checkpoint API (beta in v1.30)
  bridges to the CRI runtime. On some containerd configurations it returns
  `rpc error: code = Unimplemented`. Podman bypasses this layer and calls CRIU directly
  — this is reliable for the E1 baseline experiment.
- **Hardware parity** is required by CRIU: cannot migrate between x86_64 ↔ ARM64.
  Key contrast point with WebAssembly's hardware-agnostic bytecode.
- **Network sockets** are not preserved: `--network=none` is deliberate. Full
  socket-preserving migration requires SDN overlays (Oakestra, MACVLAN) — documented
  as a finding, not a bug.
- **The K8s API path** (kubelet checkpoint → checkpointctl build → restore pod) is the
  production-grade approach described in the Kubernetes Checkpoint/Restore WG. Use it
  when the containerd runtime fully supports NRI-based checkpointing.


## References:
- https://canonical.com/multipass
- https://github.com/todoroff/terraform-provider-multipass
- https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli
- https://kubernetes.io/blog/2026/01/21/introducing-checkpoint-restore-wg/
- https://criu.org/Installation
- https://podman.io/docs/checkpoint
- https://github.com/checkpoint-restore/checkpointctl
- https://criu.org/Kubernetes
- https://criu.org/Installation
- https://criu.org/Live_migration
- https://github.com/checkpoint-restore/criu/tree/v4.2?tab=readme-ov-file