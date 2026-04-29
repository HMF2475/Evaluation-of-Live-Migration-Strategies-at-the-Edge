# Podman + CRIU Container Migration Baseline

This document is the current Podman+CRIU baseline for this repository.

It is not the main CRIU benchmark path. Main cold/pre-copy/post-copy experiments use native CRIU under `Container/scripts/orchestrators/`, `Game-of-life-migration/`, and `Network-live-migration/`.

Podman baseline purpose:

- show container-runtime checkpoint/restore with CRIU;
- compare native process migration against a container runtime path;
- append one cold checkpoint/restore row to `Container/metrics/migration_metrics.csv`;
- document why Kubernetes-native migration is not used as the main experiment path.

## Status

- Active as a baseline.
- Memory-only.
- Cold checkpoint/restore only.
- Uses a root Podman container because Podman checkpointing requires root containers.
- Uses `--network=none` deliberately.
- Does not preserve TCP sockets, Kubernetes Service IPs, volumes, or cluster-managed Pod identity.

## What This Demonstrates

The baseline migrates a simple counter container:

```text
edge-node-1                         edge-node-2
-----------                         -----------
podman run counter
counter prints 0,1,2,...
podman checkpoint --export
archive transfer
                                    podman restore --import
                                    counter continues from saved state
```

If the restored container logs continue at or above the expected next value, process memory state survived migration.

## What This Does Not Demonstrate

This baseline does not demonstrate:

- Kubernetes Pod live migration;
- Kubernetes kubelet Checkpoint API;
- pre-copy migration;
- post-copy/lazy-pages migration;
- network socket preservation;
- persistent volume consistency;
- Service/VIP/Ingress continuity.

For established TCP sockets, use:

- `Network-live-migration/TCP-live-migration.md`
- `Network-live-migration/CRIU-limitations.md`

For native CRIU strategies, use:

- `Container/CRIU-COLD-MIGRATION.md`
- `Container/CRIU-PRE-COPY.md`
- `Container/CRIU-POST-COPY.md`

## Prerequisites

From repository root:

```bash
cd tools/terraform
terraform init
terraform apply -auto-approve
cd ../..
```

Run canonical readiness check:

```bash
bash tools/terraform/check_bootstrap.sh
```

The script waits until all three nodes are reachable, `node-bootstrap` completed, and CRIU/Podman are available.

Podman checkpoint/restore currently requires:

- root container;
- CRIU installed on source and destination;
- compatible architecture and runtime;
- destination able to restore the same container runtime metadata;
- no unsupported kernel/resource features inside the container.

Official Podman docs:

- Podman checkpoint overview: https://podman.io/docs/checkpoint
- `podman container checkpoint`: https://docs.podman.io/en/stable/markdown/podman-container-checkpoint.1.html
- `podman container restore`: https://docs.podman.io/en/latest/markdown/podman-container-restore.1.html

## Quick Automated Run

This is the recommended path for metrics.

1. Reset old Podman state:

```bash
for n in edge-node-1 edge-node-2; do
  multipass exec "$n" -- bash -lc '
    sudo podman rm -f counter 2>/dev/null || true
    sudo podman system reset -f
    sudo rm -f /tmp/counter-checkpoint.tar.zst /home/ubuntu/counter-checkpoint.tar.zst
  '
done
```

2. Start source container:

```bash
multipass exec edge-node-1 -- sudo podman run -d \
  --name counter \
  --network=none \
  --security-opt apparmor=unconfined \
  busybox:latest \
  sh -c 'i=0; while true; do echo $i; i=$((i+1)); sleep 1; done'
```

Why these options:

- `--network=none`: keeps this baseline memory-only and avoids network namespace/TCP complications.
- `--security-opt apparmor=unconfined`: avoids AppArmor profile name mismatch between Ubuntu nodes during restore.
- `busybox:latest`: tiny image, simple counter, easy continuity check.

3. Run migration and record metrics:

```bash
bash Container/scripts/orchestrators/collect_podman_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter \
  --transfer-mode host \
  --run-id podman-host-cold-001 \
  --csv Container/metrics/migration_metrics.csv
```

Direct VM-to-VM transfer:

```bash
bash Container/scripts/orchestrators/collect_podman_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter \
  --transfer-mode direct \
  --run-id podman-direct-cold-001 \
  --csv Container/metrics/migration_metrics.csv
```

4. Verify logs on destination:

```bash
multipass exec edge-node-2 -- sudo podman logs --tail=20 counter
multipass exec edge-node-2 -- sudo podman ps
```

Expected:

- counter does not restart from zero;
- `success=true` is appended to CSV only if observed value is at least the expected next value;
- metrics row includes `migration_method=cold` and `technology=CRIU`.

## Manual Run

Use this when explaining phases by hand.

### 1. Start Container

```bash
multipass exec edge-node-1 -- sudo podman run -d \
  --name counter \
  --network=none \
  --security-opt apparmor=unconfined \
  busybox:latest \
  sh -c 'i=0; while true; do echo $i; i=$((i+1)); sleep 1; done'
```

Watch:

```bash
multipass exec edge-node-1 -- sudo podman logs -f counter
```

### 2. Checkpoint on Source

```bash
LAST_BEFORE=$(multipass exec edge-node-1 -- sudo podman logs --tail=200 counter | \
  awk '/^[0-9]+$/{v=$1} END{print v}')
echo "Last value before checkpoint: ${LAST_BEFORE}"

T_CHECKPOINT_START=$(date +%s%N)
multipass exec edge-node-1 -- sudo podman container checkpoint \
  --export=/tmp/counter-checkpoint.tar.zst \
  counter
T_CHECKPOINT_DONE=$(date +%s%N)

CHECKPOINT_MS=$(( (T_CHECKPOINT_DONE - T_CHECKPOINT_START) / 1000000 ))
echo "Checkpoint time: ${CHECKPOINT_MS} ms"
```

Podman stops the source container after checkpoint. The checkpoint archive is:

```text
/tmp/counter-checkpoint.tar.zst
```

### 3. Stage Archive

```bash
multipass exec edge-node-1 -- bash -lc '
  sudo cp /tmp/counter-checkpoint.tar.zst /home/ubuntu/counter-checkpoint.tar.zst
  sudo chown ubuntu:ubuntu /home/ubuntu/counter-checkpoint.tar.zst
'
```

Optional host inspection:

```bash
HOST_CKPT="$PWD/counter-checkpoint.tar.zst"
rm -f "$HOST_CKPT"
multipass transfer edge-node-1:/home/ubuntu/counter-checkpoint.tar.zst "$HOST_CKPT"

ls -lh "$HOST_CKPT"
checkpointctl show "$HOST_CKPT" || true
```

`checkpointctl` is optional. It is useful for inspecting checkpoint archives, but not required for migration.

### 4. Transfer Archive

Host-mediated transfer:

```bash
HOST_CKPT="$PWD/counter-checkpoint.tar.zst"
rm -f "$HOST_CKPT"

T_TRANSFER_START=$(date +%s%N)
multipass transfer edge-node-1:/home/ubuntu/counter-checkpoint.tar.zst "$HOST_CKPT"
multipass transfer "$HOST_CKPT" edge-node-2:/home/ubuntu/counter-checkpoint.tar.zst
T_TRANSFER_DONE=$(date +%s%N)

TRANSFER_MS=$(( (T_TRANSFER_DONE - T_TRANSFER_START) / 1000000 ))
echo "Transfer time: ${TRANSFER_MS} ms"
```

Direct transfer is implemented in `collect_podman_metrics.sh`. Use the script when you need `--transfer-mode direct`.

### 5. Restore on Destination

```bash
multipass exec edge-node-2 -- bash -lc '
  sudo cp /home/ubuntu/counter-checkpoint.tar.zst /tmp/counter-checkpoint.tar.zst
  sudo podman rm -f counter 2>/dev/null || true
'

T_RESTORE_START=$(date +%s%N)
multipass exec edge-node-2 -- sudo podman container restore \
  --import=/tmp/counter-checkpoint.tar.zst \
  --name counter
T_RESTORE_DONE=$(date +%s%N)

RESTORE_MS=$(( (T_RESTORE_DONE - T_RESTORE_START) / 1000000 ))
echo "Restore time: ${RESTORE_MS} ms"
```

Check continuity:

```bash
sleep 2
OBSERVED_AFTER=$(multipass exec edge-node-2 -- sudo podman logs --tail=1 counter | tr -d '\r')
EXPECTED_NEXT=$((LAST_BEFORE + 1))

echo "Expected next value: ${EXPECTED_NEXT}"
echo "Observed after restore: ${OBSERVED_AFTER}"
```

Valid result:

```text
Observed after restore >= Expected next value
```

Observed can be higher because the restored container keeps running before the log is read.

## Metrics

The script appends to:

```text
Container/metrics/migration_metrics.csv
```

Important fields:

| Field | Meaning |
|-------|---------|
| `technology` | `CRIU` |
| `migration_method` | `cold` |
| `network_migration` | `no` |
| `checkpoint_ms` | Podman checkpoint/export wall-clock time |
| `archive_bytes` | checkpoint archive size |
| `transfer_ms` | archive transfer wall-clock time |
| `restore_ms` | Podman restore/import wall-clock time |
| `downtime_ms` | `checkpoint_ms + transfer_ms + restore_ms` |
| `success` | true only when destination counter continues at/after expected value |
| `notes` | container name, transfer mode, expected/observed values |

This row is schema-compatible with the native CRIU CSV, but it is a Podman cold baseline. Do not compare it as pre-copy or post-copy.

## Why No Kubernetes Cluster Here

Kubernetes setup was removed from this guide because it made the baseline look like Kubernetes live migration.

Correct framing:

- This file: Podman+CRIU container checkpoint/restore baseline.
- `GUIDE.md`: repo-level benchmark map.

Kubernetes kubelet Checkpoint API is beta, but end-to-end transparent Pod live migration still needs:

- runtime support;
- checkpoint image packaging;
- scheduler/controller coordination;
- network identity handling;
- storage consistency;
- security policy for memory dumps.

This Podman baseline bypasses kubelet/CRI and calls Podman/CRIU directly so the experiment can isolate checkpoint, transfer, and restore phases.

## Common Failures

### Source container missing

Error:

```text
Source container not found on edge-node-1: counter
```

Fix:

```bash
multipass exec edge-node-1 -- sudo podman run -d \
  --name counter \
  --network=none \
  --security-opt apparmor=unconfined \
  busybox:latest \
  sh -c 'i=0; while true; do echo $i; i=$((i+1)); sleep 1; done'
```

### CRIU or Podman unavailable

Fix:

```bash
bash tools/terraform/check_bootstrap.sh
```

### Restore hangs or container remains Created

Likely AppArmor profile mismatch.

Fix:

```bash
for n in edge-node-1 edge-node-2; do
  multipass exec "$n" -- sudo podman rm -f counter 2>/dev/null || true
done
```

Restart container with:

```bash
--security-opt apparmor=unconfined
```

Then checkpoint/restore again.

### Restore says name already exists

Fix:

```bash
multipass exec edge-node-2 -- sudo podman rm -f counter
```

### Architecture mismatch

CRIU/Podman checkpoint restore requires compatible architecture and kernel/runtime support. Do not expect x86_64 checkpoints to restore on ARM64.

## References

- Podman checkpoint overview: https://podman.io/docs/checkpoint
- Podman checkpoint man page: https://docs.podman.io/en/stable/markdown/podman-container-checkpoint.1.html
- Podman restore man page: https://docs.podman.io/en/latest/markdown/podman-container-restore.1.html
- CRIU main page: https://criu.org/Main_Page
- Kubernetes Kubelet Checkpoint API: https://kubernetes.io/docs/reference/node/kubelet-checkpoint-api/
- Kubernetes Checkpoint/Restore WG: https://kubernetes.io/blog/2026/01/21/introducing-checkpoint-restore-wg/
