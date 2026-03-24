# Setup Scripts

Node initialization and cleanup tools.

## Scripts

### reset_nodes.py
Prepares both nodes for a fresh migration experiment.

**Usage**:
```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
```

**Performs**:
1. Kills any running counter/app processes
2. Removes old PID files
3. Cleans checkpoint directories
4. Clears CRIU log files
5. Removes old archives

**Idempotent**: Safe to run multiple times

## Prerequisites

Before running any migration experiment:
```bash
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2
```

This ensures clean state and no interference from prior runs.
