# Orchestrators

Main test runners that automate complete migration experiments. This directory contains the refactored CRIU benchmarking framework with modularized strategy classes and critical downtime bug fixes.

## Architecture

The framework uses a strategy pattern to implement different migration methods:

```
orchestrators/
├── criu_benchmark.py          - Main entry point (CLI + orchestration)
├── migration_strategy.py       - Abstract base class
├── cold_migration.py           - Cold migration strategy
├── precopy_migration.py        - Precopy migration strategy 
├── postcopy_migration.py       - Postcopy migration strategy (stub)
├── multipass_command.py        - VM command execution wrapper
├── ssh_utils.py                - SSH/SCP utilities
└── metrics.py                  - Unified metrics dataclass
```

## Scripts

### criu_benchmark.py
Automated CRIU migration benchmarking tool with modularized strategy implementations.

**Strategies**: 
- `cold` - Immediate checkpoint/restore (full downtime)
- `precopy` - Live migration with pre-dumps (reduced downtime, downtime calculation fixed)
- `postcopy` - On-demand paging (⚠️ TODO: not yet implemented)

**Usage**:
```bash
# Cold migration
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id cold-run-1

# Precopy live migration 
python3 Container/scripts/orchestrators/criu_benchmark.py precopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --run-id precopy-run-1 \
  --iterations 2

# Direct VM-to-VM transfer (instead of host-mediated)
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 \
  --dest edge-node-2 \
  --transfer-mode direct
```

**Features**:
- ✓ Automatic metrics collection
- ✓ CSV output for analysis
- ✓ Multiple transfer modes (host-mediated or direct SSH/SCP)
- ✓ Comprehensive error reporting
- ✓ Modularized strategy implementations
- ✓ **Fixed precopy downtime calculation** (separates pre-dump from final-dump)

**Transfer Modes**:
- `host` (default) - Source → Host machine → Destination (via multipass transfer)
- `direct` - Source → Destination directly via SSH/SCP (requires network connectivity between VMs)

### collect_podman_metrics.sh
Podman+CRIU container migration benchmarking.

**Usage**:
```bash
bash Container/scripts/orchestrators/collect_podman_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter
```

**Features**:
- Container checkpoint/restore
- Automatic metrics logging
- Unified CSV schema with CRIU benchmarks

## Metrics Output

All scripts write metrics to: `Container/metrics/migration_metrics.csv`

Unified CSV schema (16 columns):
```
run_id,technology,migration_method,network_migration,checkpoint_ms,archive_bytes,
transfer_ms,restore_ms,downtime_ms,bandwidth_mbps,src_arch,dst_arch,same_arch,
success,notes,timestamp
```

**Key fields**:
- `checkpoint_ms` - For precopy: final dump time only (not including pre-dumps)
- `downtime_ms` - checkpoint_ms + transfer_ms + restore_ms (actual unavailability window)
- `notes` - Transfer mode (e.g., `transfer_mode=direct`) and any errors

See `Container/metrics/README.md` for complete schema documentation.
