#!/usr/bin/env bash
# tools/setup.sh — Bootstrap CRIU for container-migration experiments.
#
# This script clones CRIU from its upstream repository into tools/criu/,
# builds it, and applies the CAP_CHECKPOINT_RESTORE capability to the binary
# so that checkpoint/restore operations work without running as root.
#
# Usage (from the repository root):
#   chmod +x tools/setup.sh
#   ./tools/setup.sh
#
# After the script completes, verify CRIU is operational with:
#   cd tools/criu && ./criu/criu check

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRIU_DIR="${SCRIPT_DIR}/criu"            # tools/criu  — CRIU source root
CRIU_REPO="https://github.com/checkpoint-restore/criu.git"
CRIU_VERSION="v3.19"                     # pin to a stable release tag

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
info()  { echo "[setup] $*"; }
error() { echo "[setup] ERROR: $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || error "'$1' is not installed. Please install it and re-run."
}

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
info "Checking build dependencies…"
for cmd in git make gcc pkg-config setcap; do
    require_cmd "$cmd"
done

# ---------------------------------------------------------------------------
# Clone / update CRIU source into tools/criu/
# ---------------------------------------------------------------------------
if [[ -d "${CRIU_DIR}/.git" ]]; then
    info "CRIU source already present — fetching tags and checking out ${CRIU_VERSION}…"
    git -C "${CRIU_DIR}" fetch --tags --quiet
    git -C "${CRIU_DIR}" checkout "${CRIU_VERSION}" --quiet
else
    info "Cloning CRIU ${CRIU_VERSION} into ${CRIU_DIR}…"
    git clone --branch "${CRIU_VERSION}" --depth 1 "${CRIU_REPO}" "${CRIU_DIR}"
fi

# ---------------------------------------------------------------------------
# Build
# After a successful build, the binary is at:
#   tools/criu/criu/criu   (i.e. ./criu/criu relative to tools/criu/)
# ---------------------------------------------------------------------------
info "Building CRIU (this may take a few minutes)…"
if ! make -C "${CRIU_DIR}" -j"$(nproc)"; then
    error "CRIU build failed. Make sure all build dependencies are installed.
       Debian/Ubuntu: sudo apt-get install -y \\
           gcc make pkg-config \\
           libprotobuf-dev libprotobuf-c-dev protobuf-c-compiler protobuf-compiler \\
           python3-protobuf libnl-3-dev libnl-route-3-dev \\
           libcap-dev libnet-dev libbsd-dev
       Fedora/RHEL: sudo dnf install -y \\
           gcc make pkgconfig \\
           protobuf-devel protobuf-c-devel protobuf-c-compiler python3-protobuf \\
           libnl3-devel libcap-devel libnet-devel libbsd-devel"
fi

# ---------------------------------------------------------------------------
# Set the CAP_CHECKPOINT_RESTORE capability
#
# This allows the binary to perform process checkpoint/restore without
# requiring full CAP_SYS_ADMIN or running as root.
#
# Equivalent manual command (run from tools/criu/):
#   setcap cap_checkpoint_restore+eip ./criu/criu
# ---------------------------------------------------------------------------
CRIU_BINARY="${CRIU_DIR}/criu/criu"

if [[ ! -f "${CRIU_BINARY}" ]]; then
    error "Build succeeded but the expected binary '${CRIU_BINARY}' was not found."
fi

info "Setting cap_checkpoint_restore+eip on '${CRIU_BINARY}'…"
if ! setcap cap_checkpoint_restore+eip "${CRIU_BINARY}" 2>/dev/null; then
    info "setcap failed (this can happen inside containers without elevated privileges)."
    info "Re-run the following command with sudo once you have the required privileges:"
    info "  sudo setcap cap_checkpoint_restore+eip ${CRIU_BINARY}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
info "Verifying capability assignment…"
getcap "${CRIU_BINARY}"

info "Running 'criu check'…"
"${CRIU_BINARY}" check && info "CRIU is operational." || {
    info "CRIU check reported warnings — review the output above."
}
