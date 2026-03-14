#!/usr/bin/env bash
# bandwidth_monitor.sh — Network bandwidth monitor for migration experiments
#
# Monitors per-interface network bandwidth during a migration experiment.
# Samples bytes sent/received at configurable intervals and writes a CSV
# and JSON summary to the results directory.
#
# Usage:
#   ./bandwidth_monitor.sh --iface eth0 --duration 120 --interval 1 --output ../../results/bw_run1.json

set -euo pipefail

IFACE="eth0"
DURATION=120
INTERVAL=1
OUTPUT_DIR="../../results"
OUTPUT_FILE=""
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

usage() {
    echo "Usage: $0 [--iface IFACE] [--duration SECS] [--interval SECS] [--output FILE]"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --iface)    IFACE="$2";    shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        --output)   OUTPUT_FILE="$2"; shift 2 ;;
        *) usage ;;
    esac
done

if [ -z "${OUTPUT_FILE}" ]; then
    mkdir -p "${OUTPUT_DIR}"
    OUTPUT_FILE="${OUTPUT_DIR}/bandwidth_${IFACE}_${TIMESTAMP}.json"
fi

CSV_FILE="${OUTPUT_FILE%.json}.csv"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Verify interface exists
if ! cat /proc/net/dev 2>/dev/null | grep -q "${IFACE}:"; then
    echo "WARNING: interface '${IFACE}' not found. Available interfaces:"
    awk 'NR>2 {print "  " $1}' /proc/net/dev
    echo "Continuing with first available interface…"
    IFACE=$(awk 'NR>2 && !/lo/ {gsub(/:/, "", $1); print $1; exit}' /proc/net/dev)
    log "Using interface: ${IFACE}"
fi

read_iface_stats() {
    awk -v iface="${IFACE}:" '$1 == iface {print $2, $3, $10, $11}' /proc/net/dev
}

get_field() { read_iface_stats | awk "{print \$$1}"; }

# --------------------------------------------------------------------------- #
# Main sampling loop
# --------------------------------------------------------------------------- #
log "Monitoring ${IFACE} for ${DURATION}s (interval: ${INTERVAL}s)"
log "Output: ${OUTPUT_FILE}"

echo "timestamp,rx_bytes,tx_bytes,rx_packets,tx_packets,rx_bps,tx_bps" > "${CSV_FILE}"

START_TIME=$(date +%s%3N)
ELAPSED=0

declare -a SAMPLES=()

prev_rx=0; prev_tx=0; prev_ts=0
first=true

while [ "${ELAPSED}" -lt "${DURATION}" ]; do
    NOW_MS=$(date +%s%3N)
    stats=$(read_iface_stats)
    rx_bytes=$(echo "${stats}" | awk '{print $1}')
    tx_bytes=$(echo "${stats}" | awk '{print $3}')
    rx_pkts=$(echo "${stats}" | awk '{print $2}')
    tx_pkts=$(echo "${stats}" | awk '{print $4}')

    rx_bps=0; tx_bps=0
    if [ "${first}" = "false" ] && [ "${prev_ts}" -gt 0 ]; then
        dt_ms=$(( NOW_MS - prev_ts ))
        if [ "${dt_ms}" -gt 0 ]; then
            rx_bps=$(( (rx_bytes - prev_rx) * 1000 / dt_ms ))
            tx_bps=$(( (tx_bytes - prev_tx) * 1000 / dt_ms ))
        fi
    fi
    first=false

    echo "${NOW_MS},${rx_bytes},${tx_bytes},${rx_pkts},${tx_pkts},${rx_bps},${tx_bps}" >> "${CSV_FILE}"

    SAMPLES+=("{\"ts_ms\":${NOW_MS},\"rx_bytes\":${rx_bytes},\"tx_bytes\":${tx_bytes},\"rx_bps\":${rx_bps},\"tx_bps\":${tx_bps}}")

    prev_rx=${rx_bytes}; prev_tx=${tx_bytes}; prev_ts=${NOW_MS}

    sleep "${INTERVAL}"
    ELAPSED=$(( ($(date +%s%3N) - START_TIME) / 1000 ))
done

# --------------------------------------------------------------------------- #
# Summarise and write JSON
# --------------------------------------------------------------------------- #
END_TIME=$(date +%s%3N)
TOTAL_DURATION_MS=$(( END_TIME - START_TIME ))

# Compute peak / average from CSV
MAX_RX_BPS=$(awk -F',' 'NR>1 && $6>max {max=$6} END {print max+0}' "${CSV_FILE}")
MAX_TX_BPS=$(awk -F',' 'NR>1 && $7>max {max=$7} END {print max+0}' "${CSV_FILE}")
AVG_RX_BPS=$(awk -F',' 'NR>1 {sum+=$6; n++} END {if(n>0) printf "%.0f", sum/n; else print 0}' "${CSV_FILE}")
AVG_TX_BPS=$(awk -F',' 'NR>1 {sum+=$7; n++} END {if(n>0) printf "%.0f", sum/n; else print 0}' "${CSV_FILE}")

FIRST_RX=$(awk -F',' 'NR==2 {print $2}' "${CSV_FILE}")
LAST_RX=$(awk -F',' 'END {print $2}' "${CSV_FILE}")
FIRST_TX=$(awk -F',' 'NR==2 {print $3}' "${CSV_FILE}")
LAST_TX=$(awk -F',' 'END {print $3}' "${CSV_FILE}")
TOTAL_RX=$(( LAST_RX - FIRST_RX ))
TOTAL_TX=$(( LAST_TX - FIRST_TX ))

SAMPLES_JSON=$(IFS=,; echo "[${SAMPLES[*]}]")

cat > "${OUTPUT_FILE}" <<EOF
{
  "timestamp": "${TIMESTAMP}",
  "interface": "${IFACE}",
  "duration_ms": ${TOTAL_DURATION_MS},
  "interval_secs": ${INTERVAL},
  "sample_count": ${#SAMPLES[@]},
  "summary": {
    "total_rx_bytes": ${TOTAL_RX},
    "total_tx_bytes": ${TOTAL_TX},
    "peak_rx_bps":    ${MAX_RX_BPS},
    "peak_tx_bps":    ${MAX_TX_BPS},
    "avg_rx_bps":     ${AVG_RX_BPS},
    "avg_tx_bps":     ${AVG_TX_BPS}
  },
  "samples": ${SAMPLES_JSON}
}
EOF

log "Done. Total RX: $(echo "scale=2; ${TOTAL_RX}/1048576" | bc) MB, TX: $(echo "scale=2; ${TOTAL_TX}/1048576" | bc) MB"
log "Peak RX: ${MAX_RX_BPS} B/s, TX: ${MAX_TX_BPS} B/s"
log "Results: ${OUTPUT_FILE}"
log "CSV:     ${CSV_FILE}"
