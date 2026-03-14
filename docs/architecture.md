# Architecture Overview

## System Architecture

The evaluation environment consists of distributed edge nodes running container
and WebAssembly (WASM) workloads, connected by a network that can be configured
to simulate tactical edge conditions (limited bandwidth, high latency, packet
loss).

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Tactical Edge Network                           │
│  (configurable: bandwidth limit, latency, packet loss via tc/netem) │
│                                                                     │
│   ┌──────────────────────┐          ┌──────────────────────┐       │
│   │    Source Node        │          │    Target Node        │       │
│   │                      │          │                      │       │
│   │  ┌────────────────┐  │  migrate │  ┌────────────────┐  │       │
│   │  │ Edge Service   │──┼──────────┼─►│ Edge Service   │  │       │
│   │  │ (container /   │  │          │  │ (container /   │  │       │
│   │  │  WASM module)  │  │          │  │  WASM module)  │  │       │
│   │  └────────────────┘  │          │  └────────────────┘  │       │
│   │                      │          │                      │       │
│   │  Runtime options:    │          │  Runtime options:    │       │
│   │  - Docker + CRIU     │          │  - Docker + CRIU     │       │
│   │  - wasmtime          │          │  - wasmtime          │       │
│   │  - wasmedge          │          │  - wasmedge          │       │
│   │  - wasmer            │          │  - wasmer            │       │
│   └──────────────────────┘          └──────────────────────┘       │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │              Experiment Controller (this host)              │   │
│   │  run_experiment.py ──► migration scripts ──► results/       │   │
│   │  collect_metrics.py ─► system_monitor.py ─► results/       │   │
│   │  analyze_results.py ─────────────────────► analysis/plots  │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Component Descriptions

### Edge Service
A stateful HTTP microservice that simulates a tactical edge workload:
- Processes incoming data requests
- Maintains a rolling request buffer (bounded to 1000 entries)
- Exposes `/health`, `/process`, `/state`, `/metrics`, `/checkpoint` endpoints
- Persists state to a JSON file for migration checkpoints

**Container version** (`container-tests/service/`):
- Python/Flask application
- State stored in `/app/state/service_state.json`
- Exposed on port 8080

**WASM version** (`wasm-tests/service/`):
- Rust application compiled to `wasm32-wasip1`
- Line-oriented JSON stdin/stdout protocol
- State stored in a configurable JSON file on the host filesystem
- Compatible with wasmtime, wasmedge, wasmer, and iwasm

### Migration Scripts
Each script orchestrates a specific migration strategy end-to-end:
- Checkpoints the service state
- Transfers required data to the target node
- Restores the service on the target
- Measures timing and data transfer at each phase
- Writes a structured JSON result file

### Metrics Collection
- `collect_metrics.py` — samples system-wide or per-process CPU/memory/network/disk
- `system_monitor.py` — background daemon for continuous monitoring
- `bandwidth_monitor.sh` — per-interface network bandwidth tracking

### Experiment Runner
`run_experiment.py` orchestrates all strategies across multiple iterations,
applies network constraints, collects results, and produces aggregated CSV/JSON.

### Analysis
- `analyze_results.py` — statistical analysis (mean, median, CI95, etc.)
- `plot_comparison.py` — matplotlib visualisations (bar charts, box plots, scatter)

## Data Flow

```
run_experiment.py
  │
  ├── [applies tc/netem network constraints]
  │
  ├── for each strategy × iteration:
  │     ├── launch migration script (bash)
  │     │     └── writes results/[strategy]_[timestamp].json
  │     └── (optionally) launch collect_metrics.py in background
  │
  ├── aggregate_results() → results/experiment_[ts].json
  └── write_csv_summary() → results/summary_[ts].csv

analyze_results.py
  └── reads results/*.json → prints table + writes analysis JSON

plot_comparison.py
  └── reads results/*.json → writes analysis/plots/*.png
```

## Network Emulation

Tactical network conditions are simulated using Linux `tc` (traffic control)
with the `netem` queuing discipline on the experiment controller node:

```bash
# Example: 10 Mbit/s bandwidth, 50 ms latency, 1% packet loss
tc qdisc add dev eth0 root netem delay 50ms loss 1%
tc qdisc add dev eth0 root handle 1: tbf rate 10mbit burst 256kbit latency 400ms
```

The `config.yaml` file exposes `network.bandwidth_limit`, `network.latency_ms`,
and `network.packet_loss_pct` settings that the experiment runner translates
into the appropriate `tc` commands.
