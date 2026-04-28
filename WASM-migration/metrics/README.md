# WASM metrics

`migration_metrics.csv` uses the same schema as the CRIU experiments.

Primary phase metrics come from:

- injected `request_server.c` log events for checkpoint and restore boundaries;
- Python transfer timers around the archive copy between nodes;
- `/proc/<pid>` snapshots using the PID emitted by `create_command`.

Generated run artifacts are stored under `run_artifacts/`, `run_logs/`, and `node_exporter/`.

Plots are written under `plots/<batch>/` by `repeat_wasm_benchmarks.py` unless
`--no-plots` is used. Each plot batch also includes
`filtered_migration_metrics.csv`, which contains only the run IDs from that batch.
