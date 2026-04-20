# Helper Scripts

## Overview

Debugging, validation, and diagnostic utilities used when migrations fail.

For end-to-end benchmark instructions, see `GUIDE.md`.

## Key Scripts

- `diagnose_migration.sh` — post-failure diagnostics for CRIU migrations.
- `validate_migration.py` — checkpoint inspection/validation using `checkpointctl`.

## Common Usage

### Diagnose a failed run

```bash
bash Game-of-life-migration/scripts/helpers/diagnose_migration.sh \
  --source edge-node-1 \
  --dest edge-node-2
```

### Validate a CRIU image directory

```bash
python3 Game-of-life-migration/scripts/helpers/validate_migration.py /tmp/CRIU-gol
```

## Notes

- `diagnose_migration.sh` is meant to be run after a failed orchestrator run (it looks for the usual dump/restore logs and output files).
- `validate_migration.py` requires `checkpointctl` to be installed.

Example “debug loop”:

```bash
# 1. Reset nodes
python3 Game-of-life-migration/scripts/setup/reset_nodes.py edge-node-1 edge-node-2

# 2. Start workload
bash Game-of-life-migration/scripts/workloads/start_gol_c.sh edge-node-1

# 3. Run migration
python3 Game-of-life-migration/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 --dest edge-node-2

# 4. If failed, diagnose
bash Game-of-life-migration/scripts/helpers/diagnose_migration.sh \
  --source edge-node-1 --dest edge-node-2
```
