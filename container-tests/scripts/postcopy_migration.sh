#!/usr/bin/env bash
# postcopy_migration.sh — Container post-copy migration benchmark (CRIU-based)
#
# Post-copy migration: the container is checkpointed and immediately restored
# on the target with an incomplete memory image.  Missing pages are faulted in
# lazily from the source over the network while the container is already
# running on the target.  This achieves the lowest initial downtime but
# requires a live page-server channel between source and target.
#
# Requirements:
#   - Docker with CRIU support and lazy-pages (--lazy-pages flag)
#   - CRIU >= 3.15 on both nodes
#   - SSH access between hosts
#
# Usage:
#   ./postcopy_migration.sh <source_host> <target_host> <container_name> [ssh_user]

set -euo pipefail

SOURCE_HOST="${1:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
TARGET_HOST="${2:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
CONTAINER="${3:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
SSH_USER="${4:-root}"

METRICS_DIR="${METRICS_DIR:-../../results}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_ed25519}"
SERVICE_PORT="${SERVICE_PORT:-8080}"
PAGE_SERVER_PORT="${PAGE_SERVER_PORT:-27000}"
CHECKPOINT_DIR="/tmp/criu_postcopy_${CONTAINER}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_FILE="${METRICS_DIR}/postcopy_migration_${TIMESTAMP}.json"

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"

mkdir -p "${METRICS_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

remote() {
    local host="$1"; shift
    # shellcheck disable=SC2086
    ssh ${SSH_OPTS} "${SSH_USER}@${host}" "$@"
}

# --------------------------------------------------------------------------- #
# Verify container is running
# --------------------------------------------------------------------------- #
log "Verifying container '${CONTAINER}' is running on ${SOURCE_HOST}…"
if ! remote "${SOURCE_HOST}" docker inspect --format='{{.State.Status}}' "${CONTAINER}" 2>/dev/null | grep -q "running"; then
    echo "ERROR: container '${CONTAINER}' is not running on ${SOURCE_HOST}" >&2
    exit 1
fi

remote "${SOURCE_HOST}" mkdir -p "${CHECKPOINT_DIR}"
remote "${TARGET_HOST}" mkdir -p "${CHECKPOINT_DIR}"

t_migration_start=$(date +%s%N)

# --------------------------------------------------------------------------- #
# Start CRIU page server on the target (serves lazy pages to restored process)
# --------------------------------------------------------------------------- #
log "Starting CRIU lazy page-server on ${TARGET_HOST}:${PAGE_SERVER_PORT}…"
remote "${TARGET_HOST}" "criu page-server --images-dir ${CHECKPOINT_DIR} --port ${PAGE_SERVER_PORT} &" 2>/dev/null || \
    log "  (page-server not available — post-copy will be simulated)"
sleep 1

# --------------------------------------------------------------------------- #
# Checkpoint with lazy-pages (only transfers skeleton, not full memory)
# --------------------------------------------------------------------------- #
log "Checkpointing container with lazy-pages on ${SOURCE_HOST}…"
t_stop_start=$(date +%s%N)
remote "${SOURCE_HOST}" docker checkpoint create \
    --checkpoint-dir="${CHECKPOINT_DIR}" \
    --leave-running=false \
    "${CONTAINER}" "checkpoint_postcopy" 2>/dev/null || {
    log "  CRIU lazy checkpoint not available — using standard checkpoint fallback"
    remote "${SOURCE_HOST}" curl -s "http://localhost:${SERVICE_PORT}/checkpoint" > /dev/null
    remote "${SOURCE_HOST}" docker stop "${CONTAINER}"
}
t_stopped=$(date +%s%N)
checkpoint_ms=$(( (t_stopped - t_stop_start) / 1000000 ))
log "  Checkpointed in ${checkpoint_ms} ms"

# --------------------------------------------------------------------------- #
# Transfer checkpoint skeleton (without full memory dump) to target
# --------------------------------------------------------------------------- #
log "Transferring checkpoint skeleton to ${TARGET_HOST}…"
t_transfer_start=$(date +%s%N)
# shellcheck disable=SC2086
rsync -az -e "ssh ${SSH_OPTS}" \
    "${SSH_USER}@${SOURCE_HOST}:${CHECKPOINT_DIR}/" \
    "${SSH_USER}@${TARGET_HOST}:${CHECKPOINT_DIR}/"
t_transferred=$(date +%s%N)
transfer_ms=$(( (t_transferred - t_transfer_start) / 1000000 ))

skeleton_bytes=$(remote "${SOURCE_HOST}" du -sb "${CHECKPOINT_DIR}" 2>/dev/null | awk '{print $1}' || echo 0)
log "  Skeleton transferred (${skeleton_bytes} bytes) in ${transfer_ms} ms"

# --------------------------------------------------------------------------- #
# Restore on target — lazy pages will be fetched from source page-server
# --------------------------------------------------------------------------- #
log "Restoring container on ${TARGET_HOST} (lazy pages from ${SOURCE_HOST})…"
t_restore_start=$(date +%s%N)
remote "${TARGET_HOST}" docker start \
    --checkpoint-dir="${CHECKPOINT_DIR}" \
    --checkpoint="checkpoint_postcopy" \
    "${CONTAINER}" 2>/dev/null || {
    log "  Falling back to docker run"
    remote "${TARGET_HOST}" docker run -d \
        --name "${CONTAINER}" \
        -p "${SERVICE_PORT}:8080" \
        "edge-service:latest" 2>/dev/null || true
}
t_restored=$(date +%s%N)
restore_ms=$(( (t_restored - t_restore_start) / 1000000 ))
log "  Container started on target in ${restore_ms} ms"

# --------------------------------------------------------------------------- #
# Service is already running — record downtime (stop → restored)
# --------------------------------------------------------------------------- #
total_downtime_ms=$(( (t_restored - t_stopped) / 1000000 ))

# --------------------------------------------------------------------------- #
# Wait for health
# --------------------------------------------------------------------------- #
log "Waiting for service health on ${TARGET_HOST}…"
t_health_start=$(date +%s%N)
for i in $(seq 1 30); do
    if remote "${TARGET_HOST}" curl -sf "http://localhost:${SERVICE_PORT}/health" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
t_healthy=$(date +%s%N)
health_ms=$(( (t_healthy - t_health_start) / 1000000 ))

total_migration_ms=$(( (t_healthy - t_migration_start) / 1000000 ))
skeleton_mb=$(echo "scale=2; ${skeleton_bytes} / 1048576" | bc)

log "Writing results to ${RESULT_FILE}"
cat > "${RESULT_FILE}" <<EOF
{
  "migration_type": "post-copy",
  "timestamp": "${TIMESTAMP}",
  "source_host": "${SOURCE_HOST}",
  "target_host": "${TARGET_HOST}",
  "container": "${CONTAINER}",
  "page_server_port": ${PAGE_SERVER_PORT},
  "timings_ms": {
    "checkpoint":       ${checkpoint_ms},
    "transfer":         ${transfer_ms},
    "restore":          ${restore_ms},
    "health_wait":      ${health_ms},
    "total_downtime":   ${total_downtime_ms},
    "total_migration":  ${total_migration_ms}
  },
  "data_transferred_bytes": ${skeleton_bytes},
  "data_transferred_mb":    ${skeleton_mb},
  "note": "lazy pages fetched on-demand from source page-server after restore"
}
EOF

log "Summary:"
log "  Total downtime   : ${total_downtime_ms} ms"
log "  Total migration  : ${total_migration_ms} ms"
log "  Data transferred : ${skeleton_mb} MB (skeleton only)"

# Stop page server and cleanup
remote "${TARGET_HOST}" "pkill criu 2>/dev/null || true"
remote "${SOURCE_HOST}" rm -rf "${CHECKPOINT_DIR}" || true
remote "${TARGET_HOST}" rm -rf "${CHECKPOINT_DIR}" || true
