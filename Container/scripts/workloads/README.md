# Workloads

Test applications for migration experiments.

## Scripts

### start_counter_c.sh
Starts a simple counter workload (recommended baseline).

**Usage**:
```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1
```

**What it does**:
1. Transfers counter.c source to VM
2. Compiles it with gcc
3. Runs compiled binary on target node
4. Creates PID files: /home/ubuntu/counter.pid, /home/ubuntu/app.pid
5. Writes counter output to /home/ubuntu/counter.log

**Output**: Simple incrementing counter (1 increment/second)

**Why C**: 
- Minimal memory footprint
- Reproducible behavior
- Easy to verify migration success

### start_tcp_echo.sh (TO BE TESTED)
TCP echo server for network-aware migration tests.

**Usage**:
```bash
bash Container/scripts/workloads/start_tcp_echo.sh edge-node-1 5000
```

**What it does**:
- Starts TCP echo server on specified port
- Listens for connections
- Echoes received data back

**Testing**: Network socket preservation during migration

### start_udp_echo.sh (TO BE TESTED)
UDP echo server for network-aware migration tests.

**Usage**:
```bash
bash Container/scripts/workloads/start_udp_echo.sh edge-node-1 5001
```

**What it does**:
- Starts UDP echo server on specified port
- Echoes received datagrams

**Testing**: UDP migration behavior

## Recommended Test Sequence

1. **Baseline**: Use counter
   ```bash
   bash Container/scripts/workloads/start_counter_c.sh edge-node-1
   ```

2. **Network tests**: Use TCP/UDP (advanced)
   ```bash
   bash Container/scripts/workloads/start_tcp_echo.sh edge-node-1 5000
   ```