#!/usr/bin/env bash
set -euo pipefail

# Part-3->8 automation for Podman+CRIU migration on Multipass nodes.
# Assumes the source container is already running (Part 3 completed).

usage() {
  cat <<'EOF'
Usage:
  bash Container/scripts/collect_podman_metrics.sh \
    --source edge-node-1 \
    --dest edge-node-2 \
    [--container counter] \
    [--archive /tmp/counter-checkpoint.tar.zst] \
    [--transfer-mode host|direct] \
    [--run-id e1-run-001] \
    [--csv Container/metrics/migration_metrics.csv]

What it does:
- Part 4: checkpoint on source
- Part 5: transfer source -> host -> destination
- Part 6: restore on destination
- Part 8: append one metrics row to CSV
EOF
}

SOURCE=""
DEST=""
CONTAINER="counter"
ARCHIVE_PATH="/tmp/counter-checkpoint.tar.zst"
TRANSFER_MODE="host"
RUN_ID="run-$(date +%Y%m%d-%H%M%S)"
CSV_FILE=""
TECHNOLOGY="CRIU"
MIGRATION_METHOD="cold"
LEGACY_SCENARIO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --container) CONTAINER="$2"; shift 2 ;;
    --archive) ARCHIVE_PATH="$2"; shift 2 ;;
    --scenario) LEGACY_SCENARIO="$2"; shift 2 ;;
    --transfer-mode) TRANSFER_MODE="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --csv) CSV_FILE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$SOURCE" || -z "$DEST" ]]; then
  echo "--source and --dest are required." >&2
  usage
  exit 1
fi

if [[ -z "$CSV_FILE" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  CSV_FILE="${SCRIPT_DIR}/../../metrics/migration_metrics.csv"
fi

if [[ "$TRANSFER_MODE" != "host" && "$TRANSFER_MODE" != "direct" ]]; then
  echo "--transfer-mode must be 'host' or 'direct'" >&2
  exit 1
fi

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

require_cmd multipass
require_cmd awk

log() {
  printf '[metrics] %s\n' "$*"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

ns_now() {
  date +%s%N
}

ms_from_ns_delta() {
  local start_ns="$1"
  local end_ns="$2"
  awk -v s="$start_ns" -v e="$end_ns" 'BEGIN { printf "%d", (e - s) / 1000000 }'
}

stream_with_progress() {
  local total_bytes="$1"

  if have_cmd pv; then
    pv -brt -s "$total_bytes"
  else
    dd bs=4M status=progress
  fi
}

last_numeric_from_logs() {
  local node="$1"
  local container="$2"
  local lines="${3:-200}"

  multipass exec "$node" -- sudo podman logs --tail "$lines" "$container" 2>/dev/null | \
    awk '/^[0-9]+$/{v=$1} END{if (v != "") print v}'
}

EXPECTED_HEADER="run_id,technology,migration_method,network_migration,checkpoint_ms,archive_bytes,transfer_ms,restore_ms,downtime_ms,bandwidth_mbps,src_arch,dst_arch,same_arch,success,notes,timestamp"

ensure_csv_schema() {
  mkdir -p "$(dirname "$CSV_FILE")"

  if [[ ! -f "$CSV_FILE" ]]; then
    printf '%s\n' "$EXPECTED_HEADER" > "$CSV_FILE"
    return
  fi

  local current_header
  current_header="$(head -n1 "$CSV_FILE" 2>/dev/null || true)"
  if [[ "$current_header" != "$EXPECTED_HEADER" ]]; then
    echo "ERROR: CSV schema mismatch in ${CSV_FILE}:" >&2
    echo "  Expected: ${EXPECTED_HEADER}" >&2
    echo "  Found:    ${current_header}" >&2
    echo "Delete or migrate ${CSV_FILE} manually before continuing." >&2
    exit 1
  fi
}

SOURCE_ARCH="$(multipass exec "$SOURCE" -- uname -m | tr -d '\r')"
DEST_ARCH="$(multipass exec "$DEST" -- uname -m | tr -d '\r')"
SAME_ARCH="false"
if [[ "$SOURCE_ARCH" == "$DEST_ARCH" ]]; then
  SAME_ARCH="true"
fi

DEST_IP="$(multipass info "$DEST" | awk '/IPv4/ {print $2; exit}')"
if [[ -z "${DEST_IP:-}" ]]; then
  echo "Failed to determine destination IP for $DEST" >&2
  exit 1
fi

log "Verifying source container exists: ${CONTAINER}"
if ! multipass exec "$SOURCE" -- sudo podman container exists "$CONTAINER"; then
  echo "Source container not found on ${SOURCE}: ${CONTAINER}" >&2
  exit 1
fi

LAST_BEFORE="$(last_numeric_from_logs "$SOURCE" "$CONTAINER" 200 || true)"
EXPECTED_NEXT="NA"
if [[ "$LAST_BEFORE" =~ ^[0-9]+$ ]]; then
  EXPECTED_NEXT=$(( LAST_BEFORE + 1 ))
fi

ARCHIVE_NAME="$(basename "$ARCHIVE_PATH")"
SOURCE_STAGE="/home/ubuntu/${ARCHIVE_NAME}"
DEST_STAGE="/home/ubuntu/${ARCHIVE_NAME}"
TMP_DIR="$(mktemp -d)"
LOCAL_ARCHIVE="${TMP_DIR}/${ARCHIVE_NAME}"

cleanup() {
  multipass exec "$SOURCE" -- sudo rm -f "$SOURCE_STAGE" "$ARCHIVE_PATH" >/dev/null 2>&1 || true
  multipass exec "$DEST" -- sudo rm -f "$DEST_STAGE" "$ARCHIVE_PATH" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

prepare_direct_ssh() {
  log "Preparing direct SSH trust (${SOURCE} -> ubuntu@${DEST_IP})"
  multipass exec "$SOURCE" -- bash -lc "mkdir -p /home/ubuntu/.ssh && chmod 700 /home/ubuntu/.ssh"
  multipass exec "$DEST" -- bash -lc "mkdir -p /home/ubuntu/.ssh && chmod 700 /home/ubuntu/.ssh"

  multipass exec "$SOURCE" -- bash -lc "test -f /home/ubuntu/.ssh/id_ed25519 || ssh-keygen -q -t ed25519 -N '' -f /home/ubuntu/.ssh/id_ed25519"
  PUBKEY="$(multipass exec "$SOURCE" -- bash -lc "cat /home/ubuntu/.ssh/id_ed25519.pub" | tr -d '\r')"
  if [[ -z "${PUBKEY:-}" ]]; then
    echo "Failed to read source SSH public key" >&2
    exit 1
  fi

  multipass exec "$DEST" -- bash -lc "grep -qxF '$PUBKEY' /home/ubuntu/.ssh/authorized_keys 2>/dev/null || echo '$PUBKEY' >> /home/ubuntu/.ssh/authorized_keys; chmod 600 /home/ubuntu/.ssh/authorized_keys"
  multipass exec "$SOURCE" -- bash -lc "ssh-keyscan -H '$DEST_IP' >> /home/ubuntu/.ssh/known_hosts 2>/dev/null || true"
  multipass exec "$SOURCE" -- bash -lc "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 ubuntu@'$DEST_IP' 'echo ok'" >/dev/null
}

if [[ "$TRANSFER_MODE" == "direct" ]]; then
  prepare_direct_ssh
fi

log "Part 4: Checkpointing ${CONTAINER} on ${SOURCE}"
T_CHECKPOINT_START_NS="$(ns_now)"
multipass exec "$SOURCE" -- sudo podman container checkpoint --export="$ARCHIVE_PATH" "$CONTAINER" >/dev/null
T_CHECKPOINT_DONE_NS="$(ns_now)"
CHECKPOINT_MS="$(ms_from_ns_delta "$T_CHECKPOINT_START_NS" "$T_CHECKPOINT_DONE_NS")"

log "Staging source archive"
multipass exec "$SOURCE" -- bash -lc "sudo cp '$ARCHIVE_PATH' '$SOURCE_STAGE' && sudo chown ubuntu:ubuntu '$SOURCE_STAGE'"
ARCHIVE_BYTES="$(multipass exec "$SOURCE" -- sudo stat -c %s "$ARCHIVE_PATH" | tr -d '\r')"
log "Archive size: ${ARCHIVE_BYTES} bytes"

log "Part 5: Transferring archive ($TRANSFER_MODE)"
T_TRANSFER_START_NS="$(ns_now)"

if [[ "$TRANSFER_MODE" == "host" ]]; then
  # Stream source VM archive to host temp file.
  log "Downloading archive from ${SOURCE} to host"
  multipass exec "$SOURCE" -- sudo cat "$SOURCE_STAGE" | stream_with_progress "$ARCHIVE_BYTES" > "$LOCAL_ARCHIVE"

  if [[ ! -s "$LOCAL_ARCHIVE" ]]; then
    echo "Local archive transfer failed or produced empty file: $LOCAL_ARCHIVE" >&2
    exit 1
  fi

  LOCAL_ARCHIVE_BYTES="$(stat -c %s "$LOCAL_ARCHIVE" | tr -d '\r')"
  if [[ "$LOCAL_ARCHIVE_BYTES" != "$ARCHIVE_BYTES" ]]; then
    echo "Downloaded archive size mismatch: expected ${ARCHIVE_BYTES}, got ${LOCAL_ARCHIVE_BYTES}" >&2
    exit 1
  fi
  log "Host staged archive size verified: ${LOCAL_ARCHIVE_BYTES} bytes"

  multipass exec "$DEST" -- bash -lc "truncate -s 0 '$DEST_STAGE'"

  # Stream host temp archive to destination VM staged path.
  log "Uploading archive from host to ${DEST}"
  cat "$LOCAL_ARCHIVE" | stream_with_progress "$ARCHIVE_BYTES" | multipass exec "$DEST" -- bash -lc "cat > '$DEST_STAGE'"
else
  # Direct VM->VM copy
  log "Uploading archive directly from ${SOURCE} to ${DEST}"
  multipass exec "$SOURCE" -- bash -lc "scp -o BatchMode=yes -o StrictHostKeyChecking=yes '$SOURCE_STAGE' ubuntu@'$DEST_IP':'$DEST_STAGE'"
fi

DEST_STAGE_BYTES="$(multipass exec "$DEST" -- sudo stat -c %s "$DEST_STAGE" | tr -d '\r')"
if [[ "$DEST_STAGE_BYTES" != "$ARCHIVE_BYTES" ]]; then
  echo "Destination staged archive size mismatch: expected ${ARCHIVE_BYTES}, got ${DEST_STAGE_BYTES}" >&2
  exit 1
fi
log "Destination staged archive size verified: ${DEST_STAGE_BYTES} bytes"

T_TRANSFER_DONE_NS="$(ns_now)"
TRANSFER_MS="$(ms_from_ns_delta "$T_TRANSFER_START_NS" "$T_TRANSFER_DONE_NS")"

log "Part 6: Restoring container on ${DEST}"
multipass exec "$DEST" -- bash -lc "sudo cp '$DEST_STAGE' '$ARCHIVE_PATH'"
multipass exec "$DEST" -- sudo podman rm -f "$CONTAINER" >/dev/null 2>&1 || true

T_RESTORE_START_NS="$(ns_now)"
multipass exec "$DEST" -- sudo podman container restore --import="$ARCHIVE_PATH" --name "$CONTAINER" >/dev/null
T_RESTORE_DONE_NS="$(ns_now)"
RESTORE_MS="$(ms_from_ns_delta "$T_RESTORE_START_NS" "$T_RESTORE_DONE_NS")"

DOWNTIME_MS=$(( CHECKPOINT_MS + TRANSFER_MS + RESTORE_MS ))
BANDWIDTH_MBPS="0"
if [[ "$TRANSFER_MS" -gt 0 ]]; then
  BANDWIDTH_MBPS="$(awk -v b="$ARCHIVE_BYTES" -v ms="$TRANSFER_MS" 'BEGIN { printf "%.2f", (b * 8) / (ms * 1000) }')"
fi

OBSERVED_AFTER="NA"
for _ in 1 2 3 4 5; do
  sleep 1
  v="$(last_numeric_from_logs "$DEST" "$CONTAINER" 20 || true)"
  if [[ "$v" =~ ^[0-9]+$ ]]; then
    OBSERVED_AFTER="$v"
    break
  fi
done

ensure_csv_schema
TIMESTAMP="$(date --iso-8601=seconds)"
SUCCESS="false"
if [[ "$OBSERVED_AFTER" =~ ^[0-9]+$ ]]; then
  SUCCESS="true"
fi
NOTES="container=${CONTAINER};transfer_mode=${TRANSFER_MODE};scenario=${LEGACY_SCENARIO:-none};last_before=${LAST_BEFORE};expected_next=${EXPECTED_NEXT};observed_after=${OBSERVED_AFTER}"

printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
  "$RUN_ID" "$TECHNOLOGY" "$MIGRATION_METHOD" "no" "$CHECKPOINT_MS" "$ARCHIVE_BYTES" "$TRANSFER_MS" "$RESTORE_MS" "$DOWNTIME_MS" \
  "$BANDWIDTH_MBPS" "$SOURCE_ARCH" "$DEST_ARCH" "$SAME_ARCH" "$SUCCESS" "$NOTES" "$TIMESTAMP" >> "$CSV_FILE"

cat <<EOF
Run recorded:
  run_id:                 $RUN_ID
  migration_method:       $MIGRATION_METHOD
  network_migration:      no
  transfer_mode:          $TRANSFER_MODE
  source/dest:            $SOURCE -> $DEST
  architecture:           $SOURCE_ARCH -> $DEST_ARCH (same_arch=$SAME_ARCH)
  checkpoint_ms:          $CHECKPOINT_MS
  archive_bytes:          $ARCHIVE_BYTES
  transfer_ms:            $TRANSFER_MS
  restore_ms:             $RESTORE_MS
  downtime_ms:            $DOWNTIME_MS
  last_before_checkpoint: $LAST_BEFORE
  expected_next:          $EXPECTED_NEXT
  observed_after_restore: $OBSERVED_AFTER
  bandwidth_mbps:         $BANDWIDTH_MBPS
  success:                $SUCCESS
  technology:             $TECHNOLOGY
  csv:                    $CSV_FILE
EOF
