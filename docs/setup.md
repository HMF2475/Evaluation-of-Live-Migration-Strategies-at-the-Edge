# Setup Guide

## Prerequisites

### System Requirements

**Experiment Controller** (the machine running the scripts):
- Linux (Ubuntu 22.04+ recommended)
- Python 3.11+
- `bash`, `ssh`, `scp`, `rsync`
- Optional: `tc` (iproute2) for network emulation

**Edge Nodes** (source and target):
- Linux with Docker installed (for container experiments)
- CRIU >= 3.15 (for pre-copy / post-copy / hybrid)
- SSH access from controller
- At least one WASM runtime for WASM experiments

### Software Dependencies

#### Python (on controller)
```bash
pip install psutil pyyaml matplotlib numpy
# Or install per-component:
pip install -r experiments/requirements.txt
pip install -r metrics/requirements.txt
pip install -r analysis/requirements.txt
```

#### Docker (on edge nodes)
```bash
# Ubuntu
curl -fsSL https://get.docker.com | sh
# Enable experimental features for checkpoint/restore
echo '{"experimental": true}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

#### CRIU (on edge nodes, for pre-copy/post-copy/hybrid)
```bash
# Ubuntu 22.04+
sudo apt install criu
criu check  # verify kernel support
```

#### WASM Runtimes (on edge nodes)

**wasmtime** (recommended):
```bash
curl https://wasmtime.dev/install.sh -sSf | bash
wasmtime --version
```

**wasmedge**:
```bash
curl -sSf https://raw.githubusercontent.com/WasmEdge/WasmEdge/master/utils/install.sh | bash
wasmedge --version
```

**wasmer**:
```bash
curl https://get.wasmer.io -sSfL | sh
wasmer --version
```

#### Rust (for building the WASM service)
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup target add wasm32-wasip1
```

## Building the Services

### Container Service
```bash
cd container-tests/service
docker build -t edge-service:latest .
# Verify it runs
docker run --rm -p 8080:8080 edge-service:latest &
curl http://localhost:8080/health
```

### WASM Service
```bash
cd wasm-tests/service
cargo build --target wasm32-wasip1 --release
ls -lh target/wasm32-wasip1/release/edge-service.wasm

# Run unit tests (native)
cargo test

# Test with wasmtime
echo '{"action":"health"}' | \
  wasmtime --dir=. target/wasm32-wasip1/release/edge-service.wasm
```

## SSH Configuration

The migration scripts connect to edge nodes over SSH. Set up key-based
authentication:

```bash
# Generate a key if needed
ssh-keygen -t ed25519 -f ~/.ssh/tee_migration

# Copy to both nodes
ssh-copy-id -i ~/.ssh/tee_migration.pub root@node-source
ssh-copy-id -i ~/.ssh/tee_migration.pub root@node-target

# Test
ssh -i ~/.ssh/tee_migration root@node-source hostname
ssh -i ~/.ssh/tee_migration root@node-target hostname
```

Update `experiments/config.yaml` with your key path:
```yaml
nodes:
  source:
    ssh_key: "~/.ssh/tee_migration"
```

## Running Tests

### Unit Tests (no external nodes required)
```bash
cd /path/to/repo
pip install psutil pyyaml
python3 -m pytest tests/ -v
```

### Single Strategy Test (manual)
```bash
# Cold migration (requires Docker on both nodes)
export SSH_KEY=~/.ssh/tee_migration
export METRICS_DIR=./results
bash container-tests/scripts/cold_migration.sh node-source node-target edge-service root

# WASM migration
bash wasm-tests/scripts/wasm_migrate.sh node-source node-target \
  wasm-tests/service/target/wasm32-wasip1/release/edge-service.wasm root
```

### Full Experiment
```bash
cd experiments
python3 run_experiment.py --config config.yaml
```

### Analysis
```bash
cd analysis
python3 analyze_results.py --results-dir ../results
python3 plot_comparison.py --results-dir ../results --output-dir plots/
```

## Troubleshooting

### CRIU checkpoint fails
- Check `criu check` output on the source node
- Ensure Docker experimental mode is enabled
- Verify `/proc/sys/kernel/unprivileged_userns_clone` is 1 (Ubuntu)

### WASM binary not found
- Run `cargo build --target wasm32-wasip1 --release` in `wasm-tests/service/`
- Verify `wasm32-wasip1` target is installed: `rustup target list --installed`

### SSH connection refused
- Verify SSH keys are copied with `ssh-copy-id`
- Check firewall rules on edge nodes
- Test manually: `ssh -v -i <key> user@host`

### Metrics collection fails
- Install psutil: `pip install psutil`
- Check Python version: `python3 --version` (requires 3.11+)
