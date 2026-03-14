#!/usr/bin/env bash
# wasm_migrate.sh — WebAssembly service migration benchmark
#
# Migrates a running WASM edge-service from source to target node by:
# 1. Triggering an application-level checkpoint (state serialisation to JSON)
# 2. Transferring the state file and WASM binary to the target
# 3. Restoring the service on the target from the checkpoint
# 4. Measuring migration time, downtime, and data transferred
#
# The WASM module communicates via stdin/stdout using a JSON line protocol.
# State is fully captured in a small JSON file, so transfer costs are minimal
# compared to full container image/memory migration.
#
# Supported runtimes: wasmtime, wasmedge, wamr (wasmer), iwasm
#
# Usage:
#   ./wasm_migrate.sh <source_host> <target_host> <wasm_binary> [ssh_user]
#
# Environment:
#   WASM_RUNTIME   — runtime to use (default: wasmtime)
#   METRICS_DIR    — results output directory
#   SSH_KEY        — path to SSH private key

set -euo pipefail

SOURCE_HOST="${1:?Usage: $0 <source_host> <target_host> <wasm_binary> [ssh_user]}"
TARGET_HOST="${2:?Usage: $0 <source_host> <target_host> <wasm_binary> [ssh_user]}"
WASM_BINARY="${3:?Usage: $0 <source_host> <target_host> <wasm_binary> [ssh_user]}"
SSH_USER="${4:-root}"

WASM_RUNTIME="${WASM_RUNTIME:-wasmtime}"
METRICS_DIR="${METRICS_DIR:-../../results}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_ed25519}"
STATE_FILE="${STATE_FILE:-/tmp/wasm_state_${RANDOM}.json}"
REMOTE_DIR="/tmp/wasm_migration_${RANDOM}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_FILE="${METRICS_DIR}/wasm_migration_${TIMESTAMP}.json"

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"

mkdir -p "${METRICS_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

remote() {
    local host="$1"; shift
    # shellcheck disable=SC2086
    ssh ${SSH_OPTS} "${SSH_USER}@${host}" "$@"
}

# --------------------------------------------------------------------------- #
# Verify WASM binary exists on source
# --------------------------------------------------------------------------- #
log "Verifying WASM binary '${WASM_BINARY}' on ${SOURCE_HOST}…"
if ! remote "${SOURCE_HOST}" test -f "${WASM_BINARY}"; then
    echo "ERROR: WASM binary not found on ${SOURCE_HOST}: ${WASM_BINARY}" >&2
    exit 1
fi

remote "${TARGET_HOST}" mkdir -p "${REMOTE_DIR}"

# --------------------------------------------------------------------------- #
# Step 1: Trigger application-level checkpoint on source
#         Send {"action":"checkpoint"} to the running process via a named pipe
# --------------------------------------------------------------------------- #
log "Triggering WASM service checkpoint on ${SOURCE_HOST}…"
t_start=$(date +%s%N)

# The WASM service reads JSON requests from a control FIFO/socket
# Here we send a checkpoint request and wait for confirmation
remote "${SOURCE_HOST}" bash -c "
    FIFO=/tmp/wasm_ctrl_\$\$
    mkfifo \"\${FIFO}\"
    STATE_FILE='${STATE_FILE}' \
    ${WASM_RUNTIME} --dir=/ '${WASM_BINARY}' < \"\${FIFO}\" &
    PID=\$!
    echo '{\"action\":\"checkpoint\"}' > \"\${FIFO}\"
    wait \$PID 2>/dev/null || true
    rm -f \"\${FIFO}\"
" 2>/dev/null || {
    # Fallback: if the service is managed as a background process, send signal
    log "  Using signal-based checkpoint fallback"
    remote "${SOURCE_HOST}" bash -c "
        if [ -f /tmp/wasm_service.pid ]; then
            kill -USR1 \$(cat /tmp/wasm_service.pid) 2>/dev/null || true
        fi
    " || true
}

t_checkpoint=$(date +%s%N)
checkpoint_ms=$(( (t_checkpoint - t_start) / 1000000 ))
log "  Checkpoint triggered in ${checkpoint_ms} ms"

# --------------------------------------------------------------------------- #
# Step 2: Stop service on source and record downtime start
# --------------------------------------------------------------------------- #
log "Stopping WASM service on ${SOURCE_HOST}…"
t_stop_start=$(date +%s%N)
remote "${SOURCE_HOST}" bash -c "
    [ -f /tmp/wasm_service.pid ] && \
    kill \$(cat /tmp/wasm_service.pid) 2>/dev/null || true
" || true
t_stopped=$(date +%s%N)
stop_ms=$(( (t_stopped - t_stop_start) / 1000000 ))
log "  Service stopped in ${stop_ms} ms"

# --------------------------------------------------------------------------- #
# Step 3: Transfer WASM binary + state file to target
# --------------------------------------------------------------------------- #
log "Measuring binary size…"
binary_bytes=$(remote "${SOURCE_HOST}" stat -c %s "${WASM_BINARY}" 2>/dev/null || echo 0)
state_bytes=0
if remote "${SOURCE_HOST}" test -f "${STATE_FILE}" 2>/dev/null; then
    state_bytes=$(remote "${SOURCE_HOST}" stat -c %s "${STATE_FILE}" 2>/dev/null || echo 0)
fi
binary_mb=$(echo "scale=2; ${binary_bytes} / 1048576" | bc)
state_kb=$(echo "scale=2; ${state_bytes} / 1024" | bc)
log "  Binary: ${binary_mb} MB | State: ${state_kb} KB"

log "Transferring to ${TARGET_HOST}…"
t_transfer_start=$(date +%s%N)
# shellcheck disable=SC2086
scp ${SSH_OPTS} "${SSH_USER}@${SOURCE_HOST}:${WASM_BINARY}" \
    "${SSH_USER}@${TARGET_HOST}:${REMOTE_DIR}/"
if [ "${state_bytes}" -gt 0 ]; then
    # shellcheck disable=SC2086
    scp ${SSH_OPTS} "${SSH_USER}@${SOURCE_HOST}:${STATE_FILE}" \
        "${SSH_USER}@${TARGET_HOST}:${REMOTE_DIR}/service_state.json"
fi
t_transferred=$(date +%s%N)
transfer_ms=$(( (t_transferred - t_transfer_start) / 1000000 ))
total_bytes=$(( binary_bytes + state_bytes ))
total_mb=$(echo "scale=2; ${total_bytes} / 1048576" | bc)
log "  Transferred ${total_mb} MB in ${transfer_ms} ms"

# --------------------------------------------------------------------------- #
# Step 4: Restore on target
# --------------------------------------------------------------------------- #
log "Restoring WASM service on ${TARGET_HOST}…"
binary_name=$(basename "${WASM_BINARY}")
t_restore_start=$(date +%s%N)

remote "${TARGET_HOST}" bash -c "
    cd '${REMOTE_DIR}'
    STATE_FILE='${REMOTE_DIR}/service_state.json' \
    ${WASM_RUNTIME} --dir=. '${REMOTE_DIR}/${binary_name}' &
    echo \$! > /tmp/wasm_service.pid
" 2>/dev/null || log "  Warning: could not start service (runtime may not be installed)"

t_restored=$(date +%s%N)
restore_ms=$(( (t_restored - t_restore_start) / 1000000 ))
log "  Service restored in ${restore_ms} ms"

# --------------------------------------------------------------------------- #
# Step 5: Verify service is operational on target (send health check)
# --------------------------------------------------------------------------- #
log "Verifying WASM service on ${TARGET_HOST}…"
t_health_start=$(date +%s%N)
health_ok="false"
for i in $(seq 1 10); do
    result=$(remote "${TARGET_HOST}" bash -c "
        echo '{\"action\":\"health\"}' | \
        STATE_FILE='${REMOTE_DIR}/service_state.json' \
        ${WASM_RUNTIME} --dir=. '${REMOTE_DIR}/${binary_name}' 2>/dev/null | head -1
    " 2>/dev/null || echo "")
    if echo "${result}" | grep -q '"status":"ok"'; then
        health_ok="true"
        break
    fi
    sleep 1
done
t_healthy=$(date +%s%N)
health_ms=$(( (t_healthy - t_health_start) / 1000000 ))
log "  Health check: ${health_ok} (${health_ms} ms)"

# --------------------------------------------------------------------------- #
# Compute totals
# --------------------------------------------------------------------------- #
total_downtime_ms=$(( (t_restored - t_stopped) / 1000000 ))
total_migration_ms=$(( (t_healthy - t_stop_start) / 1000000 ))

log "Writing results to ${RESULT_FILE}"
cat > "${RESULT_FILE}" <<EOF
{
  "migration_type": "wasm",
  "timestamp": "${TIMESTAMP}",
  "source_host": "${SOURCE_HOST}",
  "target_host": "${TARGET_HOST}",
  "wasm_binary": "${WASM_BINARY}",
  "wasm_runtime": "${WASM_RUNTIME}",
  "timings_ms": {
    "checkpoint":       ${checkpoint_ms},
    "stop":             ${stop_ms},
    "transfer":         ${transfer_ms},
    "restore":          ${restore_ms},
    "health_wait":      ${health_ms},
    "total_downtime":   ${total_downtime_ms},
    "total_migration":  ${total_migration_ms}
  },
  "data_transferred_bytes": {
    "binary": ${binary_bytes},
    "state":  ${state_bytes},
    "total":  ${total_bytes}
  },
  "data_transferred_mb": ${total_mb},
  "health_check_passed": ${health_ok}
}
EOF

log "Summary:"
log "  Runtime          : ${WASM_RUNTIME}"
log "  Total downtime   : ${total_downtime_ms} ms"
log "  Total migration  : ${total_migration_ms} ms"
log "  Data transferred : ${total_mb} MB (binary + state)"
log "  Health check     : ${health_ok}"
