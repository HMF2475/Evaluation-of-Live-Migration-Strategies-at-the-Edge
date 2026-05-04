# WASM metrics

`migration_metrics.csv` keeps the common timing columns used by the CRIU experiments
and adds WASM/checkpoint precision columns where available.

Primary phase metrics come from:

- injected `request_server.c` log events for checkpoint and restore boundaries;
- Python transfer timers around archive creation, host/relay/direct copy legs, cleanup, and destination unpack;
- `/proc/<pid>` snapshots using the PID emitted by `create_command`.

New rows include detailed transfer fields:

- `archive_create_ms`
- `transfer_setup_ms`
- `transfer_send_ms`
- `transfer_receive_ms`
- `transfer_cleanup_ms`
- `unpack_ms`

Generated run artifacts are stored under `run_artifacts/`, `run_logs/`, and `node_exporter/`.

Plots are written under `plots/<batch>/` by `repeat_wasm_benchmarks.py` unless
`--no-plots` is used. Each plot batch also includes
`filtered_migration_metrics.csv`, which contains only the run IDs from that batch.
New batches also include `transfer_phase_breakdown.png` when the detailed transfer
fields are present.

## Reading `transfer_phase_breakdown.png`

Each stacked bar is the mean transfer-side cost for WASM host/direct transfer mode:

- `archive create`: package `main_memory.b` and `checkpoint_memory.b` into `wasm-state.tar.gz`.
- `transfer setup`: orchestration before copying data, such as destination IP lookup, source-file validation, SSH trust setup, SSH test connection, or host temp-file creation.
- `copy leg 1`: first file-copy operation. In direct mode this is source VM to destination VM. In host mode this is source VM to relay VM. 
- `copy leg 2`: second file-copy operation, only for relay/host mode. In host mode this is relay VM to destination VM. Direct mode normally has zero here.
- `cleanup`: removal of temporary host files or relay-staged files.
- `destination unpack`: extract the WASM state archive and place the memory files where the destination runtime expects them.

Use this plot to explain whether high `transfer_ms` comes from real data movement or from fixed overhead around the transfer. For tiny WASM state archives, setup and unpack can be larger than the actual copy.
