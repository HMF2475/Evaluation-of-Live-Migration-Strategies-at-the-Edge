#!/usr/bin/env bash
# runtime_compat.sh — WASM runtime compatibility check
#
# Tests whether the edge-service.wasm binary runs correctly on the
# available WASM runtimes on this host.  Useful for verifying
# "heterogeneous hardware" portability before running migration experiments.
#
# Tested runtimes: wasmtime, wasmedge, wasmer, iwasm (wamr)
#
# Usage:
#   ./runtime_compat.sh <path/to/edge-service.wasm>

set -euo pipefail

WASM_BINARY="${1:?Usage: $0 <path/to/edge-service.wasm>}"
RESULTS_DIR="${RESULTS_DIR:-../../results}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_FILE="${RESULTS_DIR}/runtime_compat_${TIMESTAMP}.json"

mkdir -p "${RESULTS_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Test request to send to the WASM module
HEALTH_REQ='{"action":"health"}'

declare -A RUNTIME_RESULTS

test_runtime() {
    local runtime="$1"
    local cmd="$2"

    if ! command -v "${runtime}" &>/dev/null; then
        RUNTIME_RESULTS["${runtime}"]='{"available":false,"passed":false,"error":"not installed"}'
        log "  ${runtime}: NOT INSTALLED"
        return
    fi

    local version
    version=$(${runtime} --version 2>&1 | head -1 || echo "unknown")

    local state_file="/tmp/compat_test_${runtime}_$$.json"
    local output
    local exit_code=0

    output=$(echo "${HEALTH_REQ}" | \
        STATE_FILE="${state_file}" \
        timeout 10 bash -c "${cmd} --dir=/tmp '${WASM_BINARY}'" 2>/dev/null) || exit_code=$?

    rm -f "${state_file}"

    if [ "${exit_code}" -eq 0 ] && echo "${output}" | grep -q '"status":"ok"'; then
        RUNTIME_RESULTS["${runtime}"]=$(printf '{"available":true,"passed":true,"version":"%s"}' "${version//\"/\\\"}")
        log "  ${runtime}: PASS (${version})"
    else
        local err="${output:-exit code ${exit_code}}"
        err="${err//\"/\\\"}"
        err="${err//$'\n'/ }"
        RUNTIME_RESULTS["${runtime}"]=$(printf '{"available":true,"passed":false,"version":"%s","error":"%s"}' \
            "${version//\"/\\\"}" "${err:0:200}")
        log "  ${runtime}: FAIL (${version}) — ${err:0:80}"
    fi
}

log "WASM Runtime Compatibility Check"
log "Binary: ${WASM_BINARY}"
log "---"

test_runtime "wasmtime"  "wasmtime --dir=."
test_runtime "wasmedge"  "wasmedge"
test_runtime "wasmer"    "wasmer --dir=."
test_runtime "iwasm"     "iwasm"

log "---"
log "Writing results to ${RESULT_FILE}"

# Build JSON output
{
    echo "{"
    echo "  \"timestamp\": \"${TIMESTAMP}\","
    echo "  \"wasm_binary\": \"${WASM_BINARY}\","
    echo "  \"host\": \"$(hostname)\","
    echo "  \"arch\": \"$(uname -m)\","
    echo "  \"os\": \"$(uname -s)\","
    echo "  \"runtimes\": {"
    first=true
    for runtime in "${!RUNTIME_RESULTS[@]}"; do
        if [ "${first}" = "true" ]; then
            first=false
        else
            echo ","
        fi
        echo -n "    \"${runtime}\": ${RUNTIME_RESULTS[${runtime}]}"
    done
    echo ""
    echo "  }"
    echo "}"
} > "${RESULT_FILE}"

log "Done."
