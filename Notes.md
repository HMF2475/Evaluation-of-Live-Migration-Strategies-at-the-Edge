# Service Migration Benchmarking

**Internal Research Notes** - Not for public/thesis release

Used for planning, tracking TODOs, and documenting design decisions. More formal documentation is in GUIDE.md and Container/*.md files.

## Research Focus

This evaluates **service migration strategies** for Tactical Edge Networks (TEE) using:
- **Container-based migration** (CRIU)
- **WebAssembly-based migration** (planned)

## Official CRIU Toolset & Approaches

### 1. **P.Haul (Live Migration - TODO)**
**Official CRIU recommendation** for production live migration.

- **Repository**: https://github.com/checkpoint-restore/go-criu
- **Language**: Go library (not a CLI tool)
- **Status**: ⚠️ NOT YET IMPLEMENTED - requires careful integration

TODO: P.Haul requires implementing Go interfaces (PhaulLocal, PhaulRemote) with proper RPC communication. 
Before adding, verify:
- Socketpair communication between source/destination
- DumpCopyRestore callback implementation
- Server/client handshake protocol
- Memory file descriptor (Memfd) coordination

Reference: `tools/go-criu/test/phaul/main.go` shows the required pattern.

---

### 2. **libsoccr (TCP Socket Preservation)**
**Official CRIU library** for TCP connection checkpoint/restore.

- **Location**: In your `tools/criu/` directory
- **Language**: C
- **Purpose**: Save and restore TCP socket state without breaking connections
- **Use**: Test networked service migration

Key capabilities:
- Preserve TCP sockets across migration
- No connection interruption
- Essential for stateful services

**Recommended approach**: Benchmark TCP socket migration overhead

---

### 3. **checkpointctl (Checkpoint Analysis)**
**Official analysis tool** for CRIU checkpoint archives.

- **Location**: Already in your `tools/checkpointctl/`
- **Purpose**: Deep analysis of checkpoint contents
- **Use**: Validate migration completeness, analyze checkpoint metadata

Key analysis:
- Checkpoint archive inspection
- Container/process state verification
- Memory usage breakdown
- Dockerfile/runtime detection

**Recommended approach**: Use checkpointctl to validate successful migrations

---

### 4. **Emerging Tools (Worth Exploring)**

#### criu-image-streamer
- **Repository**: https://github.com/checkpoint-restore/criu-image-streamer
- **Purpose**: Stream CRIU images during migration (network optimization)
- **Relevance**: Could reduce transfer time in bandwidth-limited TEE

#### criu-coordinator
- **Repository**: https://github.com/checkpoint-restore/criu-coordinator
- **Purpose**: Orchestrate multi-node CRIU migration
- **Relevance**: Direct application to multi-node edge network benchmarking

---

## Benchmarking Strategy

### Phase 1: Baseline (Cold Migration - Memory Only)
**Purpose**: Establish reference metrics

**Method**:
- Use raw CRIU commands (dump → transfer → restore)
- Measure: checkpoint time, transfer time, restore time, total downtime
- Application: Simple counter process (memory-only)

**Comparison**:
- Standard mode
- With optimizations (--skip-file-rwx-check, etc.)

---

### Phase 2: Live Migration (P.Haul Approach - TODO)

**Status**: Not yet implemented. Requires Go library integration.

**Method**:
- Use P.Haul library (official recommendation)
- Pre-copy iterations (configurable)
- Measure: downtime reduction, overhead of pre-copies, total migration time

**Comparison**:
- 1 pre-copy vs 2 vs 3 iterations
- Compare vs cold migration baseline
- Effectiveness for different memory sizes

---

### Phase 3: Network-Aware Migration

**Status**: TCP/UDP echo server workloads available, socket migration untested.

**Method A: TCP Socket Preservation**
- Use CRIU's native socket checkpoint (--skip-file-rwx-check already applied)
- Application: TCP echo server (start_tcp_echo.sh)
- Measure: Socket continuity, connection preservation success rate
- Known issue: Open file descriptors must be replicated on destination (counter.log pattern)

**Method B: Network Streaming**
- Explore criu-image-streamer for large checkpoints
- Measure: Transfer optimization, bandwidth usage

**Current limitation**: Socket migration not yet tested. Need to verify if TCP sockets can be checkpointed and restored across nodes.

---

### Phase 4: Multi-Node Orchestration (Optional)
**Purpose**: Real-world edge network scenario

**Tools**: criu-coordinator (if stable)
**Scenario**: Orchestrated migration across 3+ nodes
**Metrics**: Orchestration overhead, coordination complexity

---

## Metrics to Collect

```
For each migration:
- Checkpoint time (dump phase)
- Archive size (data transferred)
- Transfer time (network phase)
- Restore time (resurrection phase)
- Total downtime (checkpoint + transfer + restore)
- Memory usage during migration
- CPU usage during migration
- Network bandwidth (for networked scenarios)
- Success/failure rate
- Tool used (raw CRIU, P.Haul, etc.)
```


---

## 9. Recommended Experiment Schedule

### Baseline Phase

- Cold migration: 10 runs (counter workload)
- Pre-copy migration: 10 runs (counter workload)
- Podman+CRIU baseline: 10 runs

### Extended Phase (Optional)

- Post-copy migration: TODO (implementation pending)
- P.Haul live migration: TODO (library integration pending)
- Network-aware benchmarks: TCP/UDP echo servers

### Suggested Progression

1. Cold native CRIU (debug and stabilize)
2. Pre-copy native CRIU (live migration baseline)
3. Podman+CRIU containers (container overhead assessment)
4. Network-aware variants (TCP/UDP socket migration)
5. Post-copy (if lazy-pages daemon is implemented)
6. P.Haul 

This progression provides a clear baseline-to-advanced comparison suitable for thesis methodology and results sections.

---

---

CRIU Cold Migration Disadvantage:
- Requires all open files (logs, data, sockets) to exist on destination
- Forces either:
  a) Shared storage overhead, OR
  b) Extra file transfer overhead 
- WebAssembly probably avoids this by having memory-only state
- This could be a key comparison metric: container overhead vs WASM

Tests:

Direct mode: 1228 ms downtime, 0.38 Mbps 
Host mode: 1229 ms downtime, 0.38 Mbps 
Both have identical downtime because the bottleneck is CRIU dump/restore, not transfer method for this small archive. Transfer mode doesn't matter at small scales but would differ at large scales.

---

## References

- **Official CRIU wiki**: https://criu.org
- **P.Haul**: https://criu.org/P.Haul
- **Live migration**: https://criu.org/Live_migration
- **libsoccr**: https://criu.org/Libsoccr
- **checkpoint-restore project**: https://github.com/checkpoint-restore/
