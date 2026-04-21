# Network Live Migration (TCP)

This module benchmarks CRIU live migration for a running TCP client with an established socket.

## Documentation Map

- `TCP-live-migration.md`: exhaustive end-to-end guide (manual flow, repeat suite, plots, troubleshooting).
- `CRIU-limitations.md`: TCP/IP constraints that explain why VIP handoff is required.
- `metrics/README.md`: metrics schema and artifact layout.
- `scripts/orchestrators/README.md`: benchmark entrypoints and strategy implementation map.
- `scripts/setup/README.md`: reset, time sync, node_exporter, and netem helpers.
- `scripts/visualization/README.md`: plot generation workflow.
- `simplest-example/README.md`: same-host CRIU sanity check.

## Core Migration Model

- The client binds to a VIP (default `10.22.132.250`).
- Migration moves the VIP from source to destination between dump and restore.
- Success criteria require socket continuity (no server-side reconnect).

For command-level usage, use `TCP-live-migration.md` as the source of truth.
