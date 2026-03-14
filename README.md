# Tactical Edge Service Migration: Containers vs WebAssembly

Benchmarking container and WebAssembly service migration strategies for
**Tactical Edge Networks**, focusing on migration time, service downtime,
data transferred, resource utilisation, and platform compatibility.

## Repository Structure

```
/container-tests/       Container migration implementations
  /service/             Dockerised edge service (Python/Flask)
  /scripts/             Migration strategy scripts
    cold_migration.sh       Cold migration (stop → export → transfer → import → start)
    precopy_migration.sh    Pre-copy migration (CRIU iterative checkpoints)
    postcopy_migration.sh   Post-copy migration (CRIU lazy pages)
    hybrid_migration.sh     Hybrid pre+post-copy migration

/wasm-tests/            WebAssembly migration implementations
  /service/             Rust WASI edge service (compiles to .wasm)
  /scripts/
    wasm_migrate.sh         State-serialisation-based WASM migration
    runtime_compat.sh       Multi-runtime compatibility check

/experiments/           Experiment orchestration
  config.yaml               Experiment configuration (nodes, strategies, network)
  run_experiment.py         Master experiment runner

/metrics/               Data collection utilities
  collect_metrics.py        CPU/memory/network/disk sampler
  migration_timer.py        Precise phase-level timing
  system_monitor.py         Background system resource daemon
  bandwidth_monitor.sh      Per-interface network bandwidth tracker

/analysis/              Analysis and visualisation
  analyze_results.py        Statistical analysis (mean, median, CI95)
  plot_comparison.py        Bar charts, box plots, trade-off scatter

/results/               Experiment outputs (JSON, CSV)
/docs/                  Architecture, methodology, setup, references
/tests/                 Unit and integration tests
```

## Quick Start

### 1. Install Python dependencies
```bash
pip install psutil pyyaml matplotlib numpy
```

### 2. Build the WASM service (requires Rust)
```bash
rustup target add wasm32-wasip1
cd wasm-tests/service && cargo build --target wasm32-wasip1 --release
```

### 3. Build the container service
```bash
docker build -t edge-service:latest container-tests/service/
```

### 4. Run unit tests
```bash
python3 -m pytest tests/ -v
```

### 5. Configure your edge nodes
```bash
cp experiments/config.yaml experiments/my_config.yaml
# Edit node hostnames, SSH keys, enabled strategies
```

### 6. Run experiments
```bash
cd experiments
python3 run_experiment.py --config my_config.yaml
```

### 7. Analyse and visualise
```bash
python3 analysis/analyze_results.py --results-dir results/
python3 analysis/plot_comparison.py --results-dir results/ --output-dir analysis/plots/
```

## Migration Strategies

| Strategy | Downtime | Data Transferred | Complexity |
|----------|----------|------------------|------------|
| **Cold** | High (~seconds) | High (full image ~100+ MB) | Low |
| **Pre-copy** | Medium | High (repeated page copies) | Medium |
| **Post-copy** | Low | Low (skeleton only) | High |
| **Hybrid** | Low | Medium | High |
| **WASM** | Very low (~ms) | Very low (state JSON ~KB) | Low |

## Metrics Collected

- **Migration time** — end-to-end duration of each migration
- **Service downtime** — time service is unavailable during migration
- **Data transferred** — bytes sent over the network
- **CPU utilisation** — during migration on source and target
- **Memory utilisation** — peak usage during migration
- **Network bandwidth** — throughput during transfer phase

## Supported WASM Runtimes

The WASM service is tested for compatibility with:
- [wasmtime](https://wasmtime.dev) (Bytecode Alliance)
- [WasmEdge](https://wasmedge.org) (CNCF)
- [wasmer](https://wasmer.io)
- [iwasm](https://github.com/bytecodealliance/wasm-micro-runtime) (WAMR)

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Evaluation Methodology](docs/methodology.md)
- [Setup Guide](docs/setup.md)
- [References](docs/references.md)

## License

MIT License — see [LICENSE](LICENSE).
