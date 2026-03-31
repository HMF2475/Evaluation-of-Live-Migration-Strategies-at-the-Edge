# Container / CRIU Migration Experiments

This directory contains the **CRIU-based migration** implementation used in this project:
- Native (non-container) CRIU migrations: cold, pre-copy, post-copy (lazy-pages, experimental)
- Podman+CRIU container migration baseline
- Repeatable benchmarking + metrics collection + plotting

## What’s Where

- `Container/scripts/setup/` — cleanup, node_exporter install/checks, time sync checks
  - See: `Container/scripts/setup/README.md`
- `Container/scripts/workloads/` — demo workloads (counter, tcp/udp echo)
  - See: `Container/scripts/workloads/README.md`
- `Container/scripts/orchestrators/` — main benchmark runners (`criu_benchmark.py`, `repeat_benchmarks.py`)
  - See: `Container/scripts/orchestrators/README.md`
- `Container/metrics/` — CSV outputs, raw logs, node_exporter snapshots, plots
  - See: `Container/metrics/README.md`
- Strategy guides (runnable, repo-specific):
  - `Container/CRIU-COLD-MIGRATION.md`
  - `Container/CRIU-PRE-COPY.md`
  - `Container/CRIU-POST-COPY.md`
- `Container/CRIU/` — short reference notes pointing to upstream CRIU docs
- `Container/PODMAN-MIGRATION.md` — copy/paste Podman+CRIU container migration demo

## Quick Start (repeatable benchmark batch)

For the exhaustive end-to-end setup (Terraform/MultiPass, node_exporter, plots, all options), use:
- `GUIDE.md`

Once nodes exist and are ready, run a batch (example: 10 runs each strategy/mode, memory-only counter):

```bash
python3 Container/scripts/orchestrators/repeat_benchmarks.py suite \
  --strategies cold,precopy,postcopy \
  --source edge-node-1 \
  --dest edge-node-2 \
  --workload counter \
  --network-migration no \
  --host-runs 10 \
  --direct-runs 10 \
  --iterations 2 \
  --snapshot-node-metrics
```

