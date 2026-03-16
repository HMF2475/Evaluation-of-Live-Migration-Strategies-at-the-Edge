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
  - Cold migration
  - Pre-copy migration
  - Post-copy migration
  - Hybrid approaches

- **WebAssembly migration**
  - Checkpoint/restore mechanisms
  - Lightweight runtime portability

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

The project integrate with orchestration framework **Oakestra** to simulate realistic edge deployment scenarios.


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

## Setup

### CRIU (Checkpoint/Restore In Userspace)

Container migration experiments rely on CRIU.  
After cloning the repository, build CRIU and set the required Linux capability
by running the setup script:

```bash
chmod +x tools/setup.sh
./tools/setup.sh
```

The script clones CRIU into `tools/criu/`, compiles it, and applies
`cap_checkpoint_restore+eip` to the binary so that checkpoint/restore
operations work without running as root.

After setup, verify CRIU is operational:

```bash
cd tools/criu && ./criu/criu check
```

#### Manually applying the capability

If you have already compiled CRIU and only need to (re-)apply the capability,
run the following from the `tools/criu/` directory:

```bash
sudo setcap cap_checkpoint_restore+eip ./criu/criu
```

Then verify:

```bash
getcap ./criu/criu
# Expected: ./criu/criu = cap_checkpoint_restore+eip
```

## License

This project is part of an academic research effort. Licensing will be defined as the project evolves.
