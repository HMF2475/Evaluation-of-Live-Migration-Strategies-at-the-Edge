#!/usr/bin/env bash
# cold_migration.sh — Container cold migration benchmark
#
# Cold migration: stop the container, export the image layer, transfer it to
# the target node via SSH/SCP, import the image there, and start a fresh
# container using the persisted application-level state.
#
# Usage:
#   ./cold_migration.sh <source_host> <target_host> <container_name> [ssh_user]
#
# Environment variables (override defaults):
#   METRICS_DIR   — directory to write timing JSON  (default: ../../results)
#   SSH_KEY       — path to SSH private key
#   SERVICE_PORT  — port the service listens on      (default: 8080)

set -euo pipefail

SOURCE_HOST="${1:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
TARGET_HOST="${2:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
CONTAINER="${3:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
SSH_USER="${4:-root}"

METRICS_DIR="${METRICS_DIR:-../../results}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_ed25519}"
SERVICE_PORT="${SERVICE_PORT:-8080}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_FILE="${METRICS_DIR}/cold_migration_${TIMESTAMP}.json"
IMAGE_TAR="/tmp/${CONTAINER}_cold_${TIMESTAMP}.tar"

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"

mkdir -p "${METRICS_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# --------------------------------------------------------------------------- #
# Helper: run command on a remote host
# --------------------------------------------------------------------------- #
remote() {
    local host="$1"; shift
    # shellcheck disable=SC2086
    ssh ${SSH_OPTS} "${SSH_USER}@${host}" "$@"
}

# --------------------------------------------------------------------------- #
# Step 0: verify the container is running on the source node
# --------------------------------------------------------------------------- #
log "Verifying container '${CONTAINER}' is running on ${SOURCE_HOST}…"
if ! remote "${SOURCE_HOST}" docker inspect --format='{{.State.Status}}' "${CONTAINER}" 2>/dev/null | grep -q "running"; then
    echo "ERROR: container '${CONTAINER}' is not running on ${SOURCE_HOST}" >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# Step 1: Checkpoint application-level state (HTTP endpoint)
# --------------------------------------------------------------------------- #
log "Triggering application-level checkpoint…"
t_start=$(date +%s%N)
remote "${SOURCE_HOST}" curl -s "http://localhost:${SERVICE_PORT}/checkpoint" > /dev/null
t_checkpoint=$(date +%s%N)
checkpoint_ms=$(( (t_checkpoint - t_start) / 1000000 ))
log "  Checkpoint done in ${checkpoint_ms} ms"

# --------------------------------------------------------------------------- #
# Step 2: Stop the container and record downtime start
# --------------------------------------------------------------------------- #
log "Stopping container on ${SOURCE_HOST}…"
t_stop_start=$(date +%s%N)
remote "${SOURCE_HOST}" docker stop "${CONTAINER}"
t_stopped=$(date +%s%N)
stop_ms=$(( (t_stopped - t_stop_start) / 1000000 ))
log "  Container stopped in ${stop_ms} ms"

# --------------------------------------------------------------------------- #
# Step 3: Commit the stopped container to an image and export it
# --------------------------------------------------------------------------- #
log "Committing container to image…"
t_commit_start=$(date +%s%N)
remote "${SOURCE_HOST}" docker commit "${CONTAINER}" "${CONTAINER}-snapshot"
t_committed=$(date +%s%N)
commit_ms=$(( (t_committed - t_commit_start) / 1000000 ))
log "  Committed in ${commit_ms} ms"

log "Exporting image to tar…"
t_export_start=$(date +%s%N)
remote "${SOURCE_HOST}" docker save "${CONTAINER}-snapshot" -o "${IMAGE_TAR}"
t_exported=$(date +%s%N)
export_ms=$(( (t_exported - t_export_start) / 1000000 ))
log "  Exported in ${export_ms} ms"

# --------------------------------------------------------------------------- #
# Step 4: Transfer image tar to target node and measure transfer size/time
# --------------------------------------------------------------------------- #
log "Transferring image to ${TARGET_HOST}…"
t_transfer_start=$(date +%s%N)
# shellcheck disable=SC2086
scp ${SSH_OPTS} "${SSH_USER}@${SOURCE_HOST}:${IMAGE_TAR}" \
    "${SSH_USER}@${TARGET_HOST}:${IMAGE_TAR}"
t_transferred=$(date +%s%N)
transfer_ms=$(( (t_transferred - t_transfer_start) / 1000000 ))

image_size_bytes=$(remote "${SOURCE_HOST}" stat -c %s "${IMAGE_TAR}")
image_size_mb=$(echo "scale=2; ${image_size_bytes} / 1048576" | bc)
log "  Transferred ${image_size_mb} MB in ${transfer_ms} ms"

# --------------------------------------------------------------------------- #
# Step 5: Load image on target and start container
# --------------------------------------------------------------------------- #
log "Loading image on ${TARGET_HOST}…"
t_load_start=$(date +%s%N)
remote "${TARGET_HOST}" docker load -i "${IMAGE_TAR}"
t_loaded=$(date +%s%N)
load_ms=$(( (t_loaded - t_load_start) / 1000000 ))
log "  Loaded in ${load_ms} ms"

log "Starting container on ${TARGET_HOST}…"
t_start_container=$(date +%s%N)
remote "${TARGET_HOST}" docker run -d \
    --name "${CONTAINER}" \
    -p "${SERVICE_PORT}:8080" \
    "${CONTAINER}-snapshot"
t_container_started=$(date +%s%N)
start_ms=$(( (t_container_started - t_start_container) / 1000000 ))
log "  Container started in ${start_ms} ms"

# --------------------------------------------------------------------------- #
# Step 6: Wait for the service to become healthy on the target
# --------------------------------------------------------------------------- #
log "Waiting for service to become healthy on ${TARGET_HOST}…"
t_health_start=$(date +%s%N)
for i in $(seq 1 30); do
    if remote "${TARGET_HOST}" curl -sf "http://localhost:${SERVICE_PORT}/health" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
t_healthy=$(date +%s%N)
health_ms=$(( (t_healthy - t_health_start) / 1000000 ))
log "  Service healthy after ${health_ms} ms"

# --------------------------------------------------------------------------- #
# Step 7: Compute totals and write results
# --------------------------------------------------------------------------- #
total_downtime_ms=$(( (t_healthy - t_stopped) / 1000000 ))
total_migration_ms=$(( (t_healthy - t_stop_start) / 1000000 ))

log "Migration complete. Writing results to ${RESULT_FILE}"

cat > "${RESULT_FILE}" <<EOF
{
  "migration_type": "cold",
  "timestamp": "${TIMESTAMP}",
  "source_host": "${SOURCE_HOST}",
  "target_host": "${TARGET_HOST}",
  "container": "${CONTAINER}",
  "timings_ms": {
    "checkpoint":  ${checkpoint_ms},
    "stop":        ${stop_ms},
    "commit":      ${commit_ms},
    "export":      ${export_ms},
    "transfer":    ${transfer_ms},
    "load":        ${load_ms},
    "start":       ${start_ms},
    "health_wait": ${health_ms},
    "total_downtime":  ${total_downtime_ms},
    "total_migration": ${total_migration_ms}
  },
  "data_transferred_bytes": ${image_size_bytes},
  "data_transferred_mb":    ${image_size_mb}
}
EOF

log "Summary:"
log "  Total downtime : ${total_downtime_ms} ms"
log "  Total migration: ${total_migration_ms} ms"
log "  Data transferred: ${image_size_mb} MB"

# Cleanup temp artefacts
remote "${SOURCE_HOST}" rm -f "${IMAGE_TAR}" || true
remote "${TARGET_HOST}" rm -f "${IMAGE_TAR}" || true
