# Helper Scripts

Debugging, validation, and diagnostic utilities used when migrations fail.

## Key Scripts

- `diagnose_migration.sh` — post-failure diagnostics for CRIU migrations.
- `validate_migration.py` — checkpoint inspection/validation using `checkpointctl`.

## Common Usage

### Diagnose a failed run

```bash
bash Network-live-migration/scripts/helpers/diagnose_migration.sh \
  --source edge-node-1 \
  --dest edge-node-2
```

### Validate a CRIU image directory

```bash
python3 Network-live-migration/scripts/helpers/validate_migration.py /tmp/CRIU-tcp-client
```

## Notes

- `diagnose_migration.sh` is meant to be run after a failed orchestrator run (it looks for the usual dump/restore logs and output files).
- `validate_migration.py` requires `checkpointctl` to be installed.

Use `Network-live-migration/TCP-live-migration.md` for the full experiment loop and invoke these helpers when a run fails.
