# Tactical Edge Service Migration: Containers vs WebAssembly

This repository contains the implementation and experiments for evaluating **service migration strategies in Tactical Edge Networks**, focusing on a comparison between **container-based migration** and **WebAssembly (WASM) migration**.

The project investigates how different runtime technologies perform when migrating services across distributed edge nodes operating in **Tactical Edge Environments (TEE)**, where networks are constrained and resources are limited.

## Motivation

Modern military and tactical systems increasingly rely on **edge computing** to process data close to the battlefield. However, tactical networks often suffer from:

- Limited bandwidth
- Intermittent connectivity
- High latency
- Heterogeneous hardware

In these environments, **efficient service migration** is essential to maintain service availability and performance when nodes move, fail, or become unreachable.

This project evaluates whether **container migration** or **WebAssembly-based migration** provides better performance and portability for such environments.

## Repository Structure

- **Container/** - Container service migration experiments, benchmarking tools, and metrics
  - `README.md` - Container benchmarking guide (Python tools, Seaborn visualization)
  - `scripts/` - Automation scripts for migration (cold, pre-copy, post-copy)
  - `metrics/` - Benchmark results and comparison matrices
  - Migration guides by strategy (cold, pre-copy, post-copy)

- **tools/** - Infrastructure and utilities
  - `terraform/` - Multipass node provisioning and Kubernetes setup

- **Papers/** - Research context and related work

## Quick Start: Container Migration Benchmarking

The Container migration toolkit provides automated benchmarking for three CRIU strategies with **two transfer modes**:

### Transfer Modes

**Mode 1: Host-mediated transfer (default, `--transfer-mode host`)**
```
Edge-Node-1 → Your Host Machine → Edge-Node-2
```
- Transfers via multipass (safer, no inter-node SSH setup needed)
- Baseline for testing

**Mode 2: Direct VM-to-VM transfer (recommended, `--transfer-mode direct`)**
```
Edge-Node-1 → Edge-Node-2 (direct SCP over SSH)
```
- Transfers checkpoint images AND application files directly
- More realistic for autonomous Tactical Edge networks
- **What is SCP?** See below ↓

### Quick Start

```bash
# 1. Setup infrastructure (one-time)
cd tools/terraform && terraform apply

# 2. For each benchmark run:
# Reset nodes
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2

# Start workload on source
bash Container/scripts/start_counter_c.sh edge-node-1

# Run cold migration with DIRECT VM-to-VM transfer (RECOMMENDED)
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --run-id my-first-test

# View results
cat Container/metrics/migration_metrics.csv

# Visualize with Seaborn
# (See Container/README.md for visualization examples)
```

Full guide: `Container/README.md`

## What is SCP? (Secure Copy Protocol)

**SCP** is a command-line tool that copies files securely between computers over SSH (Secure Shell).

### How it works in this project:

```bash
# Copy counter binary from edge-node-1 to edge-node-2
scp ubuntu@edge-node-1:/tmp/counter ubuntu@edge-node-2:/tmp/counter

# Copy checkpoint images
scp ubuntu@edge-node-1:/home/ubuntu/CRIU-counter.tar.gz ubuntu@edge-node-2:/home/ubuntu/
```

### Why we use it:

1. **Security** - Uses encrypted SSH connection (not plain FTP)
2. **Direct transfer** - Node-to-node without host intermediary
3. **CRIU standard** - Recommended in official CRIU migration documentation
4. **Tactical edge suitable** - Works over unreliable networks with SSH already available

### Direct vs Host-mediated transfer:

| Feature | Host-mediated | Direct (SCP) |
|---------|---|---|
| **Path** | Node → Host → Node | Node → Node |
| **Requires** | Multipass | SSH trust |
| **Speed** | Slower (2 hops) | Faster (1 hop) |
| **Real migration** | ❌ No | ✅ Yes (like production) |
| **Tactical edge ready** | ❌ No | ✅ Yes |

The direct mode automatically sets up SSH trust between nodes, so you don't need to configure it manually!

## Watching Live Migration in Real Time

To see the counter migrate from edge-node-1 to edge-node-2, use this **3-terminal setup**:

### Terminal 1: Watch source node (edge-node-1)

```bash
multipass exec edge-node-1 -- tail -f /home/ubuntu/counter.log
```

You'll see:
```
0
1
2
3
4
5
...
```
*Counter increments continuously, then STOPS during migration*

### Terminal 2: Watch destination node (edge-node-2)

```bash
multipass exec edge-node-2 -- tail -f /home/ubuntu/counter.log
```

You'll see:
```
(empty initially)
...
(shows nothing during checkpoint)
9          ← Process RESTORED with counter value 9 and continues!
10
11
...
```
*Empty until migration completes, then counter continues from source value*

### Terminal 3: Run the migration benchmark

```bash
# Reset nodes first
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2

# Start counter on source
bash Container/scripts/start_counter_c.sh edge-node-1

# Wait a few seconds for counter to increment
sleep 5

# Run migration (watch terminals 1 & 2 while this runs)
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct \
  --run-id live-demo
```

### What you'll see:

**Before migration:**
```
Terminal 1: 0, 1, 2, 3, 4, 5, 6, 7, 8, 9...
Terminal 2: (empty)
```

**During migration (checkpoint + transfer):**
```
Terminal 1: (STOPS, process frozen)
Terminal 2: (still empty)
```

**After migration (restore + continue):**
```
Terminal 1: (empty, process no longer running)
Terminal 2: Shows counter continuing! 10, 11, 12...
```

The **gap** between the last value on Terminal 1 and first value on Terminal 2 is your **downtime**! In the example above, downtime ≈ 1 second (roughly 8→13 values missed during checkpoint+transfer+restore).

### Tips for clear observation:

- Make terminals side-by-side so you can watch both simultaneously
- Start `tail -f` BEFORE starting the counter (to capture from beginning)
- Use `--run-id` to track which metrics correspond to which run
- Check CSV results: `tail -1 Container/metrics/migration_metrics.csv`

This demonstrates that CRIU has successfully migrated the **entire process state** including:
- Memory contents (counter value)
- File descriptors (stdout/log)
- Process context

## Repository Guides

To avoid duplicated instructions, refer to the specific guides:

- Infrastructure provisioning: `tools/terraform/README.md`
- Container migration benchmarking: `Container/README.md`
- Metrics and comparison: `Container/metrics/README.md`

## Objectives

The main goal is to **benchmark and compare service migration mechanisms** for Tactical Edge Networks:

Metrics:
- Migration time (checkpoint, transfer, restore)
- Service downtime
- Data transfer volume
- Architecture compatibility
- Strategy performance (cold vs. live migration)

Comparison:
- **Container migration** using CRIU checkpoint/restore
  - Cold migration 
  - Pre-copy live migration 
  - Post-copy lazy migration 
  
- **WebAssembly migration** (planned)

## Research Context

This work supports **adaptive computing and service orchestration in Tactical Edge Networks**, where services must be migrated efficiently across constrained, distributed nodes based on:

- Network conditions
- Node availability
- Mission requirements

## Evaluation Methodology

Each migration approach is tested under controlled scenarios to measure:

1. Migration latency
2. Service downtime
3. Network overhead
4. Resource utilization
5. Platform portability

Results are analyzed to determine suitability for resource-constrained edge environments.

## Related Research Areas

- Edge Computing
- Tactical Edge Networks
- Service Migration
- Container Checkpoint/Restore (CRIU)
- WebAssembly at the Edge
- Distributed Systems

## License

This project is released under the MIT License. See `LICENSE`.
