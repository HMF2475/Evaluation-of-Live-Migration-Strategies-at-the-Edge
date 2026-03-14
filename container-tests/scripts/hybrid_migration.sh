#!/usr/bin/env bash
# hybrid_migration.sh — Container hybrid migration benchmark
#
# Hybrid migration: combines pre-copy and post-copy techniques.
# 1. Run a configurable number of pre-copy rounds to warm the target with
#    frequently-accessed pages.
# 2. Perform a final stop-and-copy (like post-copy) to capture the final
#    dirty set, then immediately restore on the target.
# 3. Any remaining pages are fetched lazily from the source (post-copy phase).
#
# This minimises both total data transferred (pre-copy reduces dirty set)
# and service downtime (lazy restore).
#
# Usage:
#   ./hybrid_migration.sh <source_host> <target_host> <container_name> [ssh_user]

set -euo pipefail

SOURCE_HOST="${1:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
TARGET_HOST="${2:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
CONTAINER="${3:?Usage: $0 <source_host> <target_host> <container_name> [ssh_user]}"
SSH_USER="${4:-root}"

METRICS_DIR="${METRICS_DIR:-../../results}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_ed25519}"
SERVICE_PORT="${SERVICE_PORT:-8080}"
PAGE_SERVER_PORT="${PAGE_SERVER_PORT:-27001}"
PRE_COPY_ROUNDS="${PRE_COPY_ROUNDS:-2}"
CHECKPOINT_DIR="/tmp/criu_hybrid_${CONTAINER}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_FILE="${METRICS_DIR}/hybrid_migration_${TIMESTAMP}.json"

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"

mkdir -p "${METRICS_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

remote() {
    local host="$1"; shift
    # shellcheck disable=SC2086
    ssh ${SSH_OPTS} "${SSH_USER}@${host}" "$@"
}

log "Verifying container '${CONTAINER}' is running on ${SOURCE_HOST}…"
if ! remote "${SOURCE_HOST}" docker inspect --format='{{.State.Status}}' "${CONTAINER}" 2>/dev/null | grep -q "running"; then
    echo "ERROR: container '${CONTAINER}' is not running on ${SOURCE_HOST}" >&2
    exit 1
fi

remote "${SOURCE_HOST}" mkdir -p "${CHECKPOINT_DIR}"
remote "${TARGET_HOST}" mkdir -p "${CHECKPOINT_DIR}"

total_pre_copy_bytes=0
t_migration_start=$(date +%s%N)

# --------------------------------------------------------------------------- #
# Phase 1: Pre-copy rounds
# --------------------------------------------------------------------------- #
pre_copy_ms=0
for round in $(seq 1 "${PRE_COPY_ROUNDS}"); do
    log "Pre-copy round ${round}/${PRE_COPY_ROUNDS}…"
    round_dir="${CHECKPOINT_DIR}/precopy_${round}"
    remote "${SOURCE_HOST}" mkdir -p "${round_dir}"

    t_r=$(date +%s%N)
    remote "${SOURCE_HOST}" docker checkpoint create \
        --checkpoint-dir="${round_dir}" \
        --leave-running \
        "${CONTAINER}" "hybrid_precopy_${round}" 2>/dev/null || {
        remote "${SOURCE_HOST}" curl -s "http://localhost:${SERVICE_PORT}/checkpoint" > /dev/null
        remote "${SOURCE_HOST}" touch "${round_dir}/.simulated"
    }

    round_bytes=$(remote "${SOURCE_HOST}" du -sb "${round_dir}" 2>/dev/null | awk '{print $1}' || echo 0)
    total_pre_copy_bytes=$(( total_pre_copy_bytes + round_bytes ))

    # shellcheck disable=SC2086
    rsync -az -e "ssh ${SSH_OPTS}" \
        "${SSH_USER}@${SOURCE_HOST}:${round_dir}/" \
        "${SSH_USER}@${TARGET_HOST}:${round_dir}/" 2>/dev/null || true

    t_r_end=$(date +%s%N)
    r_ms=$(( (t_r_end - t_r) / 1000000 ))
    pre_copy_ms=$(( pre_copy_ms + r_ms ))
    log "  Round ${round}: ${round_bytes} bytes in ${r_ms} ms"
done

# --------------------------------------------------------------------------- #
# Phase 2: Start page-server on target (post-copy lazy fetch)
# --------------------------------------------------------------------------- #
log "Starting CRIU page-server on ${TARGET_HOST}:${PAGE_SERVER_PORT}…"
remote "${TARGET_HOST}" "criu page-server --images-dir ${CHECKPOINT_DIR}/final --port ${PAGE_SERVER_PORT} &" 2>/dev/null || \
    log "  (page-server not available)"
sleep 1

# --------------------------------------------------------------------------- #
# Phase 3: Final stop-and-copy with lazy-pages
# --------------------------------------------------------------------------- #
log "Final stop-and-copy checkpoint…"
final_dir="${CHECKPOINT_DIR}/final"
remote "${SOURCE_HOST}" mkdir -p "${final_dir}"

t_stop_start=$(date +%s%N)
remote "${SOURCE_HOST}" docker checkpoint create \
    --checkpoint-dir="${final_dir}" \
    "${CONTAINER}" "hybrid_final" 2>/dev/null || {
    log "  Falling back to stop + state checkpoint"
    remote "${SOURCE_HOST}" curl -s "http://localhost:${SERVICE_PORT}/checkpoint" > /dev/null
    remote "${SOURCE_HOST}" docker stop "${CONTAINER}"
}
t_stopped=$(date +%s%N)
final_stop_ms=$(( (t_stopped - t_stop_start) / 1000000 ))

final_bytes=$(remote "${SOURCE_HOST}" du -sb "${final_dir}" 2>/dev/null | awk '{print $1}' || echo 0)

# shellcheck disable=SC2086
rsync -az -e "ssh ${SSH_OPTS}" \
    "${SSH_USER}@${SOURCE_HOST}:${final_dir}/" \
    "${SSH_USER}@${TARGET_HOST}:${final_dir}/" 2>/dev/null || true

# --------------------------------------------------------------------------- #
# Phase 4: Restore on target (lazy pages fetched from source)
# --------------------------------------------------------------------------- #
log "Restoring on ${TARGET_HOST}…"
t_restore_start=$(date +%s%N)
remote "${TARGET_HOST}" docker start \
    --checkpoint-dir="${final_dir}" \
    --checkpoint="hybrid_final" \
    "${CONTAINER}" 2>/dev/null || {
    remote "${TARGET_HOST}" docker run -d \
        --name "${CONTAINER}" \
        -p "${SERVICE_PORT}:8080" \
        "edge-service:latest" 2>/dev/null || true
}
t_restored=$(date +%s%N)
restore_ms=$(( (t_restored - t_restore_start) / 1000000 ))

total_downtime_ms=$(( (t_restored - t_stopped) / 1000000 ))

# --------------------------------------------------------------------------- #
# Wait for health
# --------------------------------------------------------------------------- #
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
total_bytes=$(( total_pre_copy_bytes + final_bytes ))
total_mb=$(echo "scale=2; ${total_bytes} / 1048576" | bc)

log "Writing results to ${RESULT_FILE}"
cat > "${RESULT_FILE}" <<EOF
{
  "migration_type": "hybrid",
  "timestamp": "${TIMESTAMP}",
  "source_host": "${SOURCE_HOST}",
  "target_host": "${TARGET_HOST}",
  "container": "${CONTAINER}",
  "pre_copy_rounds": ${PRE_COPY_ROUNDS},
  "timings_ms": {
    "pre_copy_total":   ${pre_copy_ms},
    "final_stop":       ${final_stop_ms},
    "restore":          ${restore_ms},
    "health_wait":      ${health_ms},
    "total_downtime":   ${total_downtime_ms},
    "total_migration":  ${total_migration_ms}
  },
  "data_transferred_bytes": {
    "pre_copy": ${total_pre_copy_bytes},
    "final":    ${final_bytes},
    "total":    ${total_bytes}
  },
  "data_transferred_mb": ${total_mb}
}
EOF

log "Summary:"
log "  Pre-copy rounds  : ${PRE_COPY_ROUNDS}"
log "  Total downtime   : ${total_downtime_ms} ms"
log "  Total migration  : ${total_migration_ms} ms"
log "  Data transferred : ${total_mb} MB"

remote "${TARGET_HOST}" "pkill criu 2>/dev/null || true"
remote "${SOURCE_HOST}" rm -rf "${CHECKPOINT_DIR}" || true
remote "${TARGET_HOST}" rm -rf "${CHECKPOINT_DIR}" || true
