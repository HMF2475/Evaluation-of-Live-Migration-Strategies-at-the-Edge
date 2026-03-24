#!/usr/bin/env bash
#
# Start a simple TCP echo server on the source node.
# Useful for benchmarking network socket migration.
#
# Usage: bash Container/scripts/start_tcp_echo.sh [node-name] [port]
# Example: bash Container/scripts/start_tcp_echo.sh edge-node-1 5000

NODE="${1:-edge-node-1}"
PORT="${2:-5000}"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "Error: PORT must be a number between 1 and 65535 (got: ${PORT})" >&2
  exit 1
fi

echo "[$(date +'%H:%M:%S')] Starting TCP echo server on $NODE:$PORT..."

multipass exec "$NODE" -- bash -lc "
cat > /home/ubuntu/tcp_echo.py << 'EOF'
#!/usr/bin/env python3
import socket
import threading

def handle_client(conn, addr):
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            conn.sendall(data)
    except:
        pass
    finally:
        conn.close()

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('0.0.0.0', $PORT))
server.listen(5)

print('TCP Echo Server listening on port $PORT')
try:
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
except KeyboardInterrupt:
    pass
finally:
    server.close()
EOF

chmod +x /home/ubuntu/tcp_echo.py
nohup python3 /home/ubuntu/tcp_echo.py >/dev/null 2>&1 &
echo \$! > /home/ubuntu/app.pid

sleep 2
echo 'TCP echo server started:'
ps -p \$(cat /home/ubuntu/app.pid) >/dev/null 2>&1 && echo '✓ Process running' || echo '✗ Process failed'
"

echo "[$(date +'%H:%M:%S')] ✓ TCP echo server running on $NODE:$PORT"
