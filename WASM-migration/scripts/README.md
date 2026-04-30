# WASM scripts map

## Main flow

1. `setup/install_wasm_node_deps.sh` prepares Multipass nodes.
2. `orchestrators/wasm_benchmark.py` runs one source-to-destination migration.
3. `orchestrators/repeat_wasm_benchmarks.py` repeats that migration and writes
   batch logs, run IDs, node snapshots, CSV rows, and plots.
4. `visualization/generate_all_plots.py` renders figures for one batch.

## Metrics flow

- `orchestrators/metrics.py` owns the CRIU-compatible CSV row format.
- `orchestrators/process_metrics.py` reads `/proc/<pid>` for process snapshots.
- `orchestrators/node_exporter_metrics.py` summarizes before/after node snapshots.
- `visualization/` reads the CSV plus optional node_exporter snapshots and writes
  `downtime_comparison.png`, `phase_breakdown.png`, `transfer_analysis.png`,
  `transfer_phase_breakdown.png`, and optional node_exporter plots.

## Entry points

```bash
# One edge-node migration
python3 WASM-migration/scripts/orchestrators/wasm_benchmark.py \
  --source edge-node-1 --dest edge-node-2 --transfer-mode host --relay-node edge-host-1

# Repeated benchmark batch with plots
python3 WASM-migration/scripts/orchestrators/repeat_wasm_benchmarks.py suite \
  --strategies cold --source edge-node-1 --dest edge-node-2 \
  --relay-node edge-host-1 --host-runs 20 --direct-runs 20 \
  --snapshot-node-metrics --profile-name "PROFILE"
```
