# WASM metrics

`migration_metrics.csv` keeps the common timing columns used by the CRIU experiments
and adds WASM/checkpoint precision columns where available.

Primary phase metrics come from:

- injected `request_server.c` log events for checkpoint boundaries;
- Python wall-clock timers around archive creation, archive transfer, destination seeding, and restore orchestration;
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

## Metric Capture Boundaries

The CSV stores the raw timings collected by the benchmark runner:

- `checkpoint_ms`, `checkpoint_us`, `checkpoint_ns`: source-side injected log delta from `request_server - checkpoint start` to `request_server - checkpoint completed`.
- `final_dump_ms`: same value as `checkpoint_ms`, kept for compatibility with the CRIU plots.
- `archive_create_ms`: Python wall-clock timer around copying `main_memory.b` and `checkpoint_memory.b` into a state directory on the source VM and creating `wasm-state.tar.gz`.
- `archive_bytes`: size of `wasm-state.tar.gz` after archive creation.
- `transfer_setup_ms`: time spent preparing the copy path, such as IP lookup, source-file checks, SSH trust setup, and SSH connectivity tests.
- `transfer_send_ms`: first archive copy. In direct mode this is source VM to destination VM; in host/relay mode this is source VM to relay/host stage.
- `transfer_receive_ms`: second archive copy. This is used by host/relay mode for relay/host stage to destination VM and is normally zero in direct mode.
- `transfer_cleanup_ms`: removal of temporary relay/host-stage files.
- `transfer_ms`: raw Python wall-clock timer around the whole transfer helper, so it includes `transfer_setup_ms`, copy leg(s), and cleanup.
- `unpack_ms`: destination `seed_destination(...)` time, which extracts the transferred archive and places the memory files where the destination runtime expects them.
- `restore_ms`: broad destination-side wall-clock timer. It starts before destination `create_command`, includes `unpack_ms`, sends `start_command`, and ends when the destination log emits `request_server - restore memory completed`.
- `downtime_ms`: raw CSV downtime, computed as `checkpoint_ms + transfer_ms + restore_ms`.
- `total_ms`: full benchmark wall-clock time, including deployment, source startup, checkpointing, transfer, restore, artifact download, and cleanup.
- `bandwidth_mbps`: archive-size estimate using raw `archive_bytes` and raw setup-inclusive `transfer_ms`.

## Plot Timing Convention

Generated plots keep the raw CSV unchanged but adjust plotted migration-window timing:

```text
plotted transfer_ms = raw transfer_ms - transfer_setup_ms + archive_create_ms
plotted downtime_ms = raw downtime_ms - transfer_setup_ms + archive_create_ms
```

This treats SSH trust, IP lookup, source-file validation, and similar setup as pre-established deployment overhead rather than part of the migration window. Archive creation is added to the plotted transfer phase because it happens after checkpointing and before restore can begin. `unpack_ms` is not added to the plotted transfer phase because, in the current WASM runner, it is already inside the broad `restore_ms` timer.

When older rows do not contain `checkpoint_ns` or `checkpoint_us`, the plot loader attempts to recover them from the downloaded `run_artifacts/<run_id>/source.log` file. This recovery only affects checkpoint precision columns used by the plots; it does not rewrite the raw CSV timing fields.

## Reading `transfer_phase_breakdown.png`

Each stacked bar is the mean setup-adjusted transfer phase for WASM host/direct transfer mode:

- `archive create`: package `main_memory.b` and `checkpoint_memory.b` into `wasm-state.tar.gz`.
- `transfer setup`: stored in the CSV but omitted from generated transfer/downtime plots because setup is treated as pre-established deployment overhead.
- `copy` / `copy leg 1`: the file-copy operation. Direct mode shows a single `copy`; host mode shows `copy leg 1` from source VM to relay VM. 
- `copy leg 2`: second file-copy operation, only for relay/host mode. It is omitted from the direct-mode legend when it is zero.
- `cleanup`: removal of temporary host files or relay-staged files. It is omitted from a mode legend when it is zero.

The small `+/-SD` labels inside or immediately above each segment show the standard deviation of that specific transfer sub-phase. Destination unpack remains part of the broad Wasm restore timer in the current benchmark, so it is not stacked in the Wasm transfer breakdown.

## Reading `phase_breakdown.png`

Each stacked bar is the mean checkpoint, setup-adjusted transfer, and broad restore time for a host/direct transfer mode. The small `+/-SD` labels inside or immediately above each segment show the standard deviation of that specific phase. In the WASM runner, destination seeding/unpack is inside the broad restore timer, so the phase plot keeps that work under restore rather than transfer.
