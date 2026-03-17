#!/usr/bin/env bash
set -euo pipefail

# Part-3->8 automation for Podman+CRIU migration on Multipass nodes.
# Assumes the source container is already running (Part 3 completed).

usage() {
  cat <<'EOF'
Usage:
  bash Container/scripts/collect_migration_metrics.sh \
    --source edge-node-1 \
    --dest edge-node-2 \
    [--container counter] \
    [--archive /tmp/counter-checkpoint.tar.zst] \
    [--scenario E1_memory_only] \
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
SCENARIO="E1_memory_only"
RUN_ID="run-$(date +%Y%m%d-%H%M%S)"
CSV_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --container) CONTAINER="$2"; shift 2 ;;
    --archive) ARCHIVE_PATH="$2"; shift 2 ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
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
  CSV_FILE="${SCRIPT_DIR}/../metrics/migration_metrics.csv"
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
require_cmd stat

log() {
  printf '[metrics] %s\n' "$*"
}

ns_now() {
  date +%s%N
}

ms_from_ns_delta() {
  local start_ns="$1"
  local end_ns="$2"
  awk -v s="$start_ns" -v e="$end_ns" 'BEGIN { printf "%d", (e - s) / 1000000 }'
}

last_numeric_from_logs() {
  local node="$1"
  local container="$2"
  local lines="${3:-200}"

  multipass exec "$node" -- sudo podman logs --tail "$lines" "$container" 2>/dev/null | \
    awk '/^[0-9]+$/{v=$1} END{if (v != "") print v}'
}

EXPECTED_HEADER="run_id,scenario,checkpoint_ms,archive_bytes,transfer_ms,restore_ms,downtime_ms,src_arch,dst_arch,same_arch"

ensure_csv_schema() {
  mkdir -p "$(dirname "$CSV_FILE")"

  if [[ ! -f "$CSV_FILE" ]]; then
    printf '%s\n' "$EXPECTED_HEADER" > "$CSV_FILE"
    return
  fi

  local current_header
  current_header="$(head -n1 "$CSV_FILE" 2>/dev/null || true)"
  if [[ "$current_header" != "$EXPECTED_HEADER" ]]; then
    local backup
    backup="${CSV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$CSV_FILE" "$backup"
    printf '%s\n' "$EXPECTED_HEADER" > "$CSV_FILE"
    log "CSV schema mismatch detected; backed up old file to ${backup} and reinitialized ${CSV_FILE}"
  fi
}

SOURCE_ARCH="$(multipass exec "$SOURCE" -- uname -m | tr -d '\r')"
DEST_ARCH="$(multipass exec "$DEST" -- uname -m | tr -d '\r')"
SAME_ARCH="false"
if [[ "$SOURCE_ARCH" == "$DEST_ARCH" ]]; then
  SAME_ARCH="true"
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
  multipass exec "$SOURCE" -- sudo rm -f "$SOURCE_STAGE" >/dev/null 2>&1 || true
  multipass exec "$DEST" -- sudo rm -f "$DEST_STAGE" "$ARCHIVE_PATH" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

log "Part 4: Checkpointing ${CONTAINER} on ${SOURCE}"
T_CHECKPOINT_START_NS="$(ns_now)"
multipass exec "$SOURCE" -- sudo podman container checkpoint --export="$ARCHIVE_PATH" "$CONTAINER" >/dev/null
T_CHECKPOINT_DONE_NS="$(ns_now)"
CHECKPOINT_MS="$(ms_from_ns_delta "$T_CHECKPOINT_START_NS" "$T_CHECKPOINT_DONE_NS")"

log "Staging source archive"
multipass exec "$SOURCE" -- bash -lc "sudo cp '$ARCHIVE_PATH' '$SOURCE_STAGE' && sudo chown ubuntu:ubuntu '$SOURCE_STAGE'"
ARCHIVE_BYTES="$(multipass exec "$SOURCE" -- sudo stat -c %s "$ARCHIVE_PATH" | tr -d '\r')"

log "Part 5: Transferring archive source -> host -> destination"
T_TRANSFER_START_NS="$(ns_now)"

# Stream source VM archive to host temp file.
multipass exec "$SOURCE" -- sudo cat "$SOURCE_STAGE" > "$LOCAL_ARCHIVE"

if [[ ! -s "$LOCAL_ARCHIVE" ]]; then
  echo "Local archive transfer failed or produced empty file: $LOCAL_ARCHIVE" >&2
  exit 1
fi

multipass exec "$DEST" -- bash -lc "truncate -s 0 '$DEST_STAGE'"

# Stream host temp archive to destination VM staged path.
cat "$LOCAL_ARCHIVE" | multipass exec "$DEST" -- bash -lc "cat > '$DEST_STAGE'"

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
printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
  "$RUN_ID" "$SCENARIO" "$CHECKPOINT_MS" "$ARCHIVE_BYTES" "$TRANSFER_MS" "$RESTORE_MS" "$DOWNTIME_MS" \
  "$SOURCE_ARCH" "$DEST_ARCH" "$SAME_ARCH" >> "$CSV_FILE"

cat <<EOF
Run recorded:
  run_id:                 $RUN_ID
  scenario:               $SCENARIO
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
  csv:                    $CSV_FILE
EOF


# TODO: DO THIS CODE IN PYTHON INSTEAD, MUCH CLEANER AND LESS ERROR-PRONE.