# Helper Scripts

Debugging, validation, and diagnostic tools.

## Scripts

### diagnose_migration.sh
Post-failure diagnostics for CRIU migrations.

**Usage**:
```bash
bash Container/scripts/helpers/diagnose_migration.sh \
  --source edge-node-1 \
  --dest edge-node-2
```

**Checks**:
1. Node connectivity
2. CRIU installation
3. Source process status
4. Dump file integrity
5. Destination log file
6. Restored process status
7. Restore logs for errors

**Use when**: Migration fails to identify root cause

### validate_migration.py
Checkpoint validation using checkpointctl.

**Usage**:
```bash
python3 Container/scripts/helpers/validate_migration.py /tmp/CRIU-counter
```

**Validates**:
- Checkpoint archive integrity
- Memory content
- Process state metadata
- File descriptor information

## Tips

For detailed debugging:
```bash
# 1. Reset nodes
python3 Container/scripts/setup/reset_nodes.py edge-node-1 edge-node-2

# 2. Start workload
bash Container/scripts/workloads/start_counter_c.sh edge-node-1

# 3. Run migration
python3 Container/scripts/orchestrators/criu_benchmark.py cold \
  --source edge-node-1 --dest edge-node-2

# 4. If failed, diagnose
bash Container/scripts/helpers/diagnose_migration.sh \
  --source edge-node-1 --dest edge-node-2
```
