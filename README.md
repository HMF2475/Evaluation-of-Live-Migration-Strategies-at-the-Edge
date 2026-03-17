# Tactical Edge Service Migration: Containers vs WebAssembly

This repository contains the implementation and experiments for evaluating **service migration strategies in Tactical Edge Networks**, focusing on a comparison between **container-based migration** and **WebAssembly (WASM) migration**.

The project investigates how different runtime technologies perform when migrating services across distributed edge nodes operating in **Tactical Edge Environments (TEE)**, where networks are constrained and resources are limited.

## Motivation

Modern military and tactical systems increasingly rely on **edge computing** to process data close to the battlefield. However, tactical networks often suffer from:

- Limited bandwidth
- Intermittent connectivity
- High latency
- Heterogeneous hardware

In these environments, **efficient service migration** is essential to maintain service availability and performance when nodes move, fail, or become unreachable.

This project evaluates whether **container migration** or **WebAssembly-based migration** provides better performance and portability for such environments.

## Repository Guides

To avoid duplicated instructions across files, use this split:

- Infrastructure provisioning: `tools/terraform/README.md`
- Manual container migration walkthrough: `Container/K8_MIGRATION_SETUP.md`
- Automated metrics script: `Container/scripts/collect_migration_metrics.sh`

## Container Experiment: Manual vs Automated

There are two valid paths for the container experiment.

1. Manual path
- Follow all relevant parts in `Container/K8_MIGRATION_SETUP.md`.

2. Automated path
- Run Part 1 and Part 2 from `Container/K8_MIGRATION_SETUP.md`.
- Run Part 3 so `counter` is running on `edge-node-1`.
- Then run the script once from repository root:

```bash
bash Container/scripts/collect_migration_metrics.sh \
  --source edge-node-1 \
  --dest edge-node-2 \
  --container counter \
  --scenario E1_memory_only \
  --run-id e1-run-001 \
  --csv Container/metrics/migration_metrics.csv
```

When using the script, do not also run manual Part 4/5/6 for the same run, because the script already performs checkpoint, transfer, restore, and CSV logging.

This container baseline is a memory-only migration experiment. It preserves in-memory
process state, but does not preserve active network sockets, external client connections,
or persistent storage state.

## Objectives

The main goal is to **benchmark and compare service migration mechanisms** using the following metrics:

- Migration time
- Service downtime
- Amount of transferred data
- CPU and memory usage
- Network bandwidth consumption
- Runtime compatibility across heterogeneous hardware

The experiments will compare:

- **Container migration**
  - Checkpoint/restore using CRIU
  - Live migration techniques

- **WebAssembly migration**
  - WASM module serialization and transfer
  - State transfer mechanisms for WASM runtimes

## Research Context

This work is part of a research project focused on **adaptive computing and service orchestration in Tactical Edge Networks**.

The experiments aim to support future **adaptive orchestration systems**, where services can be proactively or reactively migrated depending on:

- network conditions
- node availability
- mission requirements

## Architecture Overview

The evaluation environment includes:

- Distributed edge nodes
- Container runtime (e.g., Docker / Kubernetes-based systems)
- WebAssembly runtime
- Service checkpoint and migration mechanisms
- Measurement tools for performance evaluation

The project integrates with the orchestration framework **Oakestra** to simulate realistic edge deployment scenarios.


## Evaluation Methodology

Each migration approach will be tested under controlled scenarios to measure:

1. Migration latency
2. Service downtime
3. Network overhead
4. Resource utilization
5. Platform portability

Results will be analyzed to determine the suitability of each approach for **resource-constrained edge environments**.


## Related Research Areas

- Edge Computing
- Tactical Edge Networks
- Service Migration
- Container Checkpoint/Restore
- WebAssembly at the Edge
- Distributed Systems

## License

This project is released under the MIT License. See `LICENSE`.
