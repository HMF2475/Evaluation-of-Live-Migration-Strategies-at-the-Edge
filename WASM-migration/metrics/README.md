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
