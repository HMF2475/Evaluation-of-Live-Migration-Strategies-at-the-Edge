# Container Service Migration Benchmarking Guide

This guide walks you through benchmarking container service migration using CRIU (Checkpoint/Restore In Userspace). The tools and scripts here automate the full migration workflow and collect metrics for analysis and visualization.

## Overview

The benchmarking toolkit supports three migration strategies:

1. **Cold Migration** - Stop, dump, transfer, restore (baseline)
2. **Pre-Copy Live Migration** - Multiple pre-dumps, then final dump and restore 
3. **Post-Copy Lazy Migration** - Dump, transfer, restore with lazy-pages 

All metrics are collected to CSV and can be visualized with Seaborn for comparison and analysis.

## Quick Start

### Step 1: Install Dependencies (One-Time)

```bash
# Python dependencies
python3 -m pip install seaborn pandas matplotlib

# Verify CRIU is installed on both nodes
multipass exec edge-node-1 -- criu --version
multipass exec edge-node-2 -- criu --version
```

### Step 2: Reset Nodes to Clean State

Before each benchmark run, reset the nodes to remove old processes and files:

```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
```

This handles:
- Killing any existing processes
- Cleaning CRIU dump directories on both nodes
- Removing old log files

### Step 3: Start Your Application

Start the application on the source node. 

**For baseline (C counter):**
```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1
```

This compiles and runs a simple C counter that increments every second. You can watch it:
```bash
multipass exec edge-node-1 -- tail -f /home/ubuntu/counter.log
```

**For network migration (TCP echo):**
```bash
bash Container/scripts/workloads/start_tcp_echo.sh edge-node-1 5000
```

**For network migration (UDP echo):**
```bash
bash Container/scripts/workloads/start_udp_echo.sh edge-node-1 5001
```

### Step 4: Run Migration Benchmark

Once the application is running on the source, execute one of the three strategies:

```bash
# Cold migration
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id cold-run-1

# Pre-copy live migration (2 pre-dumps)
python3 Container/scripts/orchestrators/criu_benchmark.py precopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id precopy-run-1 \
  --iterations 2

# Post-copy lazy migration
python3 Container/scripts/orchestrators/criu_benchmark.py postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id postcopy-run-1
```

### Step 5: Check Results

```bash
cat Container/metrics/migration_metrics.csv
```

## Visualizing Results

After collecting multiple benchmark runs, analyze and visualize the results using the provided visualization scripts.

### Prerequisites

Install visualization dependencies:

```bash
pip3 install pandas seaborn matplotlib
```

### Run All Visualizations

```bash
cd Container/scripts/visualization

python3 plot_downtime.py ../../metrics/migration_metrics.csv
python3 plot_transfer_analysis.py ../../metrics/migration_metrics.csv
python3 plot_phase_breakdown.py ../../metrics/migration_metrics.csv
```

### Generated Charts

**Downtime Comparison** (`plot_downtime.py`)
- Bar chart comparing total downtime across migration methods
- Shows impact of strategy (cold vs precopy vs postcopy)
- Grouped by network migration mode

**Transfer Analysis** (`plot_transfer_analysis.py`)
- Scatter plot of archive size vs transfer duration
- Identifies network bottleneck characteristics
- Colored by migration method

**Phase Breakdown** (`plot_phase_breakdown.py`)
- Stacked bar chart showing time in each phase
- Visualizes checkpoint + transfer + restore durations
- Average values per migration method

### Script Details

See `Container/scripts/visualization/README.md` for full documentation and custom usage.

## CSV Schema

Results are stored in: `Container/metrics/migration_metrics.csv`

Columns:
- `run_id` - Unique identifier for this run
- `technology` - CRIU
- `migration_method` - cold, precopy, postcopy
- `network_migration` - yes/no (socket-preserving vs memory-only migration)
- `checkpoint_ms` - Time to dump process (milliseconds). For precopy, this is the **final dump only** when service actually freezes, not including pre-dumps
- `archive_bytes` - Size of transferred data
- `transfer_ms` - Time to transfer
- `restore_ms` - Time to restore
- `downtime_ms` - Total downtime (checkpoint_ms + transfer_ms + restore_ms). **For precopy, this correctly excludes pre-dump time** since the service was still running during pre-dumps
- `bandwidth_mbps` - Effective transfer bandwidth
- `src_arch` - Source architecture
- `dst_arch` - Destination architecture
- `same_arch` - true if architectures match
- `success` - migration success/failure
- `notes` - Any errors or observations
- `timestamp` - run timestamp

## Running Multiple Benchmarks

```bash
# Run 3 cold migrations with different runs
for i in 1 2 3; do
  echo "=== Run $i ==="
  
  # Reset nodes
  python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
  
  # Start application (C counter)
  bash Container/scripts/workloads/start_counter_c.sh edge-node-1
  sleep 2
  
  # Run benchmark
  python3 Container/scripts/orchestrators/criu_benchmark.py cold \
    --source edge-node-1 \
    --dest edge-node-2 \
    --run-id "cold-run-$i"
  
  sleep 5
done

# View all results
cat Container/metrics/migration_metrics.csv
```

---

## Troubleshooting

### "ERROR: PID file not found on source"
You forgot to start the application. Run:
```bash
bash Container/scripts/workloads/start_counter_c.sh edge-node-1  
```

### "Process has stale PID"
The application exited or timed out. Reset and restart:
```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
bash Container/scripts/workloads/start_counter_c.sh edge-node-1  
```

### "ERROR: Dump failed"
Check CRIU compatibility. Common causes:
- Process has unsupported features (file handles, network sockets without --ext-net-map)
- Insufficient permissions (ensure sudo works without password)
- CRIU not fully installed on source node

Run the diagnostic:
```bash
bash Container/scripts/helpers/diagnose_migration.sh --source edge-node-1 --dest edge-node-2
```

### "ERROR: Restore failed"
Check the detailed restore log printed in output. Common causes:
- File permission mismatches (handled by --skip-file-rwx-check)
- AppArmor/SELinux restrictions
- Missing library dependencies on destination

### "LOG_READ_FAILED" or counter shows no values
Verify the destination has the log file:
```bash
multipass exec edge-node-2 -- cat /home/ubuntu/counter.log
```

## Diagnostic Tool

For detailed setup validation, use:

```bash
bash Container/scripts/helpers/diagnose_migration.sh --source edge-node-1 --dest edge-node-2
```

This checks:
- Node connectivity
- CRIU availability and version
- Source process state
- CRIU dump files
- Destination file permissions
- Restore logs for errors

## Transfer Modes: Host vs Direct

The `--transfer-mode` argument controls how checkpoint archives are transferred between source and destination nodes. Both modes are supported for all migration strategies (cold, precopy, postcopy).

### Host Transfer Mode (Default)

```bash
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode host
```

**How it works:**
1. Source node creates checkpoint archive
2. Source node transfers archive to **local machine** (via SCP)
3. Local machine transfers archive to **destination node** (via SCP)
4. Destination unpacks archive and restores

**Characteristics:**
- **Path:** source → localhost → destination
- **Advantages:**
  - Works even if source and destination can't reach each other directly
  - Good for networks with firewall restrictions between nodes
  - Useful for debugging (can inspect archives on localhost)
- **Disadvantages:**
  - Extra hop through localhost adds transfer time
  - Higher bandwidth usage on localhost
  - Requires SSH access from localhost to both nodes
- **Best for:** Firewall-restricted networks, development/debugging
- **Typical overhead:** +20-30% transfer time vs direct mode

### Direct Transfer Mode

```bash
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct
```

**How it works:**
1. Source node creates checkpoint archive
2. Source node transfers archive **directly** to destination node (via SCP)
3. Destination unpacks archive and restores

**Characteristics:**
- **Path:** source → destination (direct)
- **Advantages:**
  - Direct connection is faster (no localhost bottleneck)
  - Lower total bandwidth usage
  - More efficient for production networks
- **Disadvantages:**
  - Requires source and destination to have direct network connectivity
  - May require SSH key setup between nodes
  - Failed transfers harder to debug (can't inspect archives on localhost)
- **Best for:** Production deployments, direct node connectivity
- **Typical overhead:** Baseline (no extra hops)

### Comparison Table

| Aspect | Host Mode | Direct Mode |
|--------|-----------|-------------|
| **Network Path** | source → localhost → dest | source → dest |
| **Transfer Time** | Slower (+20-30%) | Faster (baseline) |
| **Bandwidth** | Higher (2 hops) | Lower (1 hop) |
| **Firewall Friendly** | Yes | Requires direct access |
| **SSH Keys** | localhost to both nodes | source to dest only |
| **Archive Inspection** | Easy (on localhost) | Harder (gone after restore) |
| **Setup Complexity** | Simple | Medium (needs node SSH trust) |
| **Use Case** | Restricted networks | Production/performance testing |

### Bandwidth Impact Example

For a 100 MB checkpoint archive:

**Host Mode:**
- source → localhost: 100 MB transferred
- localhost → dest: 100 MB transferred
- **Total:** 200 MB across your network

**Direct Mode:**
- source → dest: 100 MB transferred
- **Total:** 100 MB across your network (50% reduction)

### Choosing Your Mode

**Use Host Mode if:**
- Your network has firewall restrictions between nodes
- Nodes can't reach each other directly
- You're on a local development machine

**Use Direct Mode if:**
- You want production-level performance
- Nodes have direct network connectivity
- You want to minimize bandwidth usage

### Default Behavior

If you don't specify `--transfer-mode`, the framework defaults to **host mode** for reliability and simplicity. For performance-critical benchmarks, explicitly use `--transfer-mode direct`.

---

## Strategy Selection Guide

### Cold Migration (N1)
- **Downtime:** Checkpoint + Transfer + Restore time
- **Process State:** Fully preserved, no data loss

### Pre-Copy Live Migration (N2)
- **Downtime:** Much shorter (final dump only)
- **Process State:** Fully preserved, continuous after restore
- **Iterations:** Adjust `--iterations` to balance transfer overhead vs final downtime

### Post-Copy Lazy Migration (N3)
- **Downtime:** Minimal (restore happens quickly, pages fetched on demand)
- **Process State:** Fully preserved, with lazy-page daemon fetching on access
- **Warning:** Requires lazy-pages infrastructure and adds latency to memory access

## Extending to Network Socket Migration

To preserve network sockets during migration, modify the CRIU commands in the scripts:

```bash
# Add to dump command:
sudo criu dump -t $PID ... --ext-net-map=ip-address:ip-address

# And to restore command:
sudo criu restore ... --ext-net-map=ip-address:ip-address
```

See `Container/CRIU-COLD-MIGRATION.md`, `Container/CRIU-PRE-COPY.md`, or `Container/CRIU-POST-COPY.md` for detailed guidance on implementing networked migration with specific strategies.


## Related Guides

- **Infrastructure Setup:** `tools/terraform/README.md`
- **Cold Migration Manual:** `Container/CRIU-COLD-MIGRATION.md`
- **Pre-Copy Manual:** `Container/CRIU-PRE-COPY.md`
- **Post-Copy Manual:** `Container/CRIU-POST-COPY.md`
- **Network Socket Migration:** See "Extending to Network Socket Migration" section above

## See migration

Terminal 1 - Watch source node (edge-node-1):
```bash
multipass exec edge-node-1 -- tail -f /home/ubuntu/counter.log
```
Terminal 2 - Watch destination node (edge-node-2):
```bash
multipass exec edge-node-2 -- tail -f /home/ubuntu/counter.log
```

Terminal 3 - Run migration benchmark:
```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2

# Then run migration
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id test-1
```


