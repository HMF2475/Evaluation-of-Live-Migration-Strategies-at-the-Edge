#!/usr/bin/env bash
# Diagnostic script to identify where cold migration fails
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/helpers/diagnose_migration.sh --source edge-node-1 --dest edge-node-2

Checks:
1. Both nodes are running and reachable
2. CRIU is installed and working
3. File permissions on destination
4. Dump/restore logs for errors
5. Process continuity
EOF
}

SOURCE=""
DEST=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown: $1"; usage; exit 1 ;;
  esac
done

[[ -z "$SOURCE" || -z "$DEST" ]] && { usage; exit 1; }

echo "════════════════════════════════════════════════════════════"
echo "DIAGNOSTIC: CRIU Cold Migration"
echo "════════════════════════════════════════════════════════════"

# Check 1: Connectivity
echo ""
echo "✓ Check 1: Node Connectivity"
multipass exec "$SOURCE" -- bash -c 'echo "  Source OK"' || { echo "  ✗ SOURCE UNREACHABLE"; exit 1; }
multipass exec "$DEST" -- bash -c 'echo "  Dest OK"' || { echo "  ✗ DEST UNREACHABLE"; exit 1; }

# Check 2: CRIU available
echo ""
echo "✓ Check 2: CRIU Installation"
multipass exec "$SOURCE" -- bash -c 'criu --version' || { echo "  ✗ CRIU NOT ON SOURCE"; exit 1; }
multipass exec "$DEST" -- bash -c 'criu --version' || { echo "  ✗ CRIU NOT ON DEST"; exit 1; }

# Check 3: Process and dump state
echo ""
echo "✓ Check 3: Process & Dump State"
PID=$(multipass exec "$SOURCE" -- bash -lc "cat /home/ubuntu/counter.pid 2>/dev/null || cat /home/ubuntu/app.pid 2>/dev/null" | tr -d '\r') || PID=""
if [[ -z "$PID" ]]; then
  echo "  ✗ NO PID FILE — need to run Part 1 first"
  exit 1
fi
echo "  PID on source: $PID"

if multipass exec "$SOURCE" -- bash -lc "kill -0 $PID" 2>/dev/null; then
  echo "  ✗ Process still running (not frozen from dump)"
else
  echo "  ✓ Process is frozen (good)"
fi

# Check 4: Dump files exist
echo ""
echo "✓ Check 4: Dump Files"
if multipass exec "$SOURCE" -- bash -lc "sudo test -d /tmp/CRIU-counter"; then
  echo "  ✓ /tmp/CRIU-counter exists"
  multipass exec "$SOURCE" -- bash -lc "sudo ls -lh /tmp/CRIU-counter/ | head -15"
else
  echo "  ✗ /tmp/CRIU-counter missing"
fi

# Check 5: Destination log file
echo ""
echo "✓ Check 5: Destination Log File"
if multipass exec "$DEST" -- bash -lc "test -f /home/ubuntu/counter.log"; then
  echo "  ✓ /home/ubuntu/counter.log exists on dest"
  echo "    Size: $(multipass exec "$DEST" -- bash -lc 'wc -l /home/ubuntu/counter.log' | awk '{print $1}') lines"
  echo "    Content:"
  multipass exec "$DEST" -- bash -lc "cat /home/ubuntu/counter.log | tail -20"
else
  echo "  ✗ /home/ubuntu/counter.log missing on dest"
fi

# Check 6: Restored PID
echo ""
echo "✓ Check 6: Restored Process"
if multipass exec "$DEST" -- bash -lc "sudo test -f /tmp/CRIU-counter/restored.pid"; then
  RESTORED_PID=$(multipass exec "$DEST" -- bash -lc "sudo cat /tmp/CRIU-counter/restored.pid" | tr -d '\r')
  echo "  Restored PID: $RESTORED_PID"
  if multipass exec "$DEST" -- bash -lc "ps -p $RESTORED_PID >/dev/null 2>&1"; then
    echo "  ✓ Process is running"
  else
    echo "  ✗ Process is NOT running"
  fi
else
  echo "  ✗ restored.pid file missing"
fi

# Check 7: Restore log
echo ""
echo "✓ Check 7: Restore Log (Last 30 lines)"
if multipass exec "$DEST" -- bash -lc "sudo test -f /tmp/CRIU-counter/restore.log"; then
  multipass exec "$DEST" -- bash -lc "sudo tail -n 30 /tmp/CRIU-counter/restore.log"
else
  echo "  ✗ restore.log missing"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "END DIAGNOSTIC"
echo "════════════════════════════════════════════════════════════"
