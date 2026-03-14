# Evaluation Methodology

## Overview

This document describes the experimental methodology used to compare
container-based and WebAssembly-based service migration strategies for
Tactical Edge Networks.

## Metrics

Each migration experiment captures the following metrics:

| Metric                  | Unit  | Description |
|-------------------------|-------|-------------|
| **Service Downtime**    | ms    | Time from service stop on source to service available on target |
| **Total Migration Time**| ms    | End-to-end time including all phases (checkpoint, transfer, restore, health-check) |
| **Data Transferred**    | MB    | Total bytes sent over the network during migration |
| **CPU Utilisation**     | %     | Average CPU usage on source and target nodes during migration |
| **Memory Utilisation**  | MB    | Peak memory used during migration process |
| **Transfer Throughput** | MB/s  | Average network throughput during data transfer phase |

## Migration Strategies

### Container Migration

#### Cold Migration
1. Trigger application-level checkpoint (write state to disk)
2. Stop the container (downtime begins)
3. Commit the stopped container to an image
4. Export the image as a `.tar` archive
5. Transfer the `.tar` to the target node via `scp`
6. Load the image on target
7. Start the container on target
8. Wait for health check (downtime ends)

**Trade-offs**: Highest downtime and data transferred; simplest to implement.

#### Pre-copy Migration (CRIU)
1. Run _N_ iterative checkpoint rounds with `--leave-running` (container stays active)
   - Each round copies changed memory pages to target
2. Final stop-and-copy: stop container, transfer remaining dirty pages
3. Restore container on target from final checkpoint
4. Wait for health check

**Trade-offs**: Longer total migration time; shorter downtime than cold migration.
Higher total data transferred due to repeated page copies.

#### Post-copy Migration (CRIU + Lazy Pages)
1. Start CRIU page-server on target
2. Take a single checkpoint (stop container)
3. Transfer checkpoint skeleton (process state without full memory dump)
4. Immediately restore on target — container starts running
5. Missing pages are fault-fetched from source page-server on-demand
6. Wait for health check

**Trade-offs**: Minimal downtime; requires active page-server channel to source.
Performance depends on network quality for subsequent page faults.

#### Hybrid Migration
Combines pre-copy and post-copy:
1. Run _N_ pre-copy rounds to warm the target with hot pages
2. Start page-server on target
3. Final stop-and-copy (small dirty set due to pre-copy warmup)
4. Restore on target with lazy page fetching for any remaining pages

**Trade-offs**: Balances downtime and total data transferred.

### WebAssembly Migration

1. Trigger application-level checkpoint (serialise state to JSON file)
2. Stop the WASM process on source
3. Transfer WASM binary + JSON state file to target
4. Start WASM process on target with state file
5. Verify service health

**Advantages**:
- State is a tiny JSON file (kilobytes vs megabytes for containers)
- Binary is architecture-independent (runs on any WASM runtime)
- No dependency on CRIU or kernel support
- Minimal downtime and data transferred

**Limitations**:
- No memory-level continuity (process restarts from serialised state)
- Binary must be pre-compiled for WASI target
- Limited syscall support vs containers

## Experimental Setup

### Node Configuration
- 2 edge nodes (source and target)
- Connected via a configurable network link (tc/netem for constraint simulation)
- Same hardware architecture (for container experiments)
- Any architecture for WASM experiments (portability test)

### Network Scenarios
Each strategy is tested under multiple network conditions:

| Scenario      | Bandwidth | Latency | Packet Loss |
|---------------|-----------|---------|-------------|
| Baseline      | Unlimited | ~1 ms   | 0%          |
| Constrained   | 10 Mbit/s | 20 ms   | 0%          |
| High Latency  | 10 Mbit/s | 100 ms  | 0%          |
| Lossy         | 5 Mbit/s  | 50 ms   | 2%          |
| Severe        | 1 Mbit/s  | 200 ms  | 5%          |

### Service Workload
The test service runs a moderate workload during migration:
- 100 HTTP requests in-flight (simulated with `wrk` or `ab`)
- 10 MB state (data buffer with ~1000 entries)
- State updated on every request

### Statistical Methodology
- **Iterations**: 5 runs per strategy per scenario (after 1 warm-up run)
- **Metrics**: Mean, median, std-dev, 95% confidence interval
- **Outlier detection**: Values > 2σ from mean flagged but included
- **Reporting**: Results reported as mean ± 95% CI

## Replication Instructions

```bash
# 1. Install dependencies
pip install -r experiments/requirements.txt
pip install -r metrics/requirements.txt
pip install -r analysis/requirements.txt

# 2. Build WASM binary (requires Rust with wasm32-wasip1 target)
cd wasm-tests/service
cargo build --target wasm32-wasip1 --release

# 3. Build container image on both nodes
docker build -t edge-service:latest container-tests/service/

# 4. Edit experiment configuration
cp experiments/config.yaml experiments/my_config.yaml
# Edit node hostnames, SSH keys, strategies

# 5. Run experiments
cd experiments
python3 run_experiment.py --config my_config.yaml

# 6. Analyse results
cd ../analysis
python3 analyze_results.py --results-dir ../results
python3 plot_comparison.py --results-dir ../results --output-dir plots/
```

## Threats to Validity

- **Internal validity**: Network emulation approximates real tactical conditions
  but may not capture all dynamics (e.g., bursty losses, multi-path routing)
- **External validity**: Results depend on service state size; larger services
  may show different relative performance
- **Construct validity**: Downtime is measured from process stop to first
  successful health check; brief transient failures after restore are included
