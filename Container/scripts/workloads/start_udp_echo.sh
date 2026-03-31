#!/usr/bin/env bash
#
# Start a simple UDP echo server on the source node.
# Useful for benchmarking network socket migration.
#
# Usage: bash Container/scripts/workloads/start_udp_echo.sh [node-name] [port]
# Example: bash Container/scripts/workloads/start_udp_echo.sh edge-node-1 5001

NODE="${1:-edge-node-1}"
PORT="${2:-5001}"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "Error: PORT must be a number between 1 and 65535 (got: ${PORT})" >&2
  exit 1
fi

echo "[$(date +'%H:%M:%S')] Starting UDP echo server on $NODE:$PORT..."

multipass exec "$NODE" -- bash -lc "
cat > /home/ubuntu/udp_echo.py << 'EOF'
#!/usr/bin/env python3
import socket

server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('0.0.0.0', $PORT))

print('UDP Echo Server listening on port $PORT')
try:
    while True:
        data, addr = server.recvfrom(1024)
        server.sendto(data, addr)
except KeyboardInterrupt:
    pass
finally:
    server.close()
EOF

chmod +x /home/ubuntu/udp_echo.py
nohup python3 /home/ubuntu/udp_echo.py >/dev/null 2>&1 &
echo \$! > /home/ubuntu/app.pid

sleep 2
echo 'UDP echo server started:'
ps -p \$(cat /home/ubuntu/app.pid) >/dev/null 2>&1 && echo '✓ Process running' || echo '✗ Process failed'
"

echo "[$(date +'%H:%M:%S')] ✓ UDP echo server running on $NODE:$PORT"
