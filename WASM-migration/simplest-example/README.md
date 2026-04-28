# Simplest local WASM migration

This example runs Edoardo Tinto's existing WASM migration. It uses the already injected modules in
`../wasm-migrate-commands/wasm_test_computation/`.

## Build commands

From repo root:

```bash
cmake -S WASM-migration/wasm-migrate-commands -B WASM-migration/wasm-migrate-commands/build
cmake --build WASM-migration/wasm-migrate-commands/build --target create_command start_command migrate_command
```

If CMake cannot find libcurl headers:

```bash
sudo apt-get install libcurl4-openssl-dev
```

## Run once

This is the recommended local path. It runs the full source checkpoint,
state archive/copy, and destination restore flow without manual IPC handling:

```bash
python3 WASM-migration/simplest-example/run_host_migration.py
```

Default module: `3mm_with_cr.wasm`.

Run another existing injected module:

```bash
python3 WASM-migration/simplest-example/run_host_migration.py \
  --module WASM-migration/wasm-migrate-commands/wasm_test_computation/floyd-warshall_with_cr.wasm
```

## What happens

1. `create_command` starts the WASM request server as a local source process.
2. `start_command` activates the source computation.
3. `migrate_command` asks the source process to checkpoint.
4. The generated `main_memory.b` and `checkpoint_memory.b` files are archived and copied locally.
5. A second local request server starts as the destination.
6. The destination restores from the copied memory files and completes the computation.

## What app is being migrated?

The default app is `3mm_with_cr.wasm`. The visible proof of migration is:

- source log reaches `checkpoint completed`;
- `main_memory.b` and `checkpoint_memory.b` are created;
- destination log reaches `restore memory completed` and `end of call`.



## Manual run without Python

Use this when you want to see each migration phase by hand. Run everything in one
terminal first. Local runs use `-` for the cgroup argument,
so no cgroup write is needed.

The manual flow is:

1. start a source request server;
2. activate the source;
3. ask the source to checkpoint;
4. package the two memory files;
5. seed a destination request server with those memory files;
6. activate the destination and verify restore.

The raw command order is:

```bash
create_command <module.wasm> <ipc> <main_memory.b> <checkpoint_memory.b> <cgroup-or-> <log>
start_command <ipc>
migrate_command <ipc>
```

### 1. Pick paths for this run

Run from repo root:

```bash
ROOT="$PWD"
CMD_DIR="$ROOT/WASM-migration/wasm-migrate-commands/build"
MODULE="$ROOT/WASM-migration/wasm-migrate-commands/wasm_test_computation/3mm_with_cr.wasm"
RUN="$ROOT/WASM-migration/simplest-example/artifacts/manual-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
echo "$RUN"
```

Keep using this same terminal. If you open another terminal, set `RUN` to the
exact printed path. Do not create a new `RUN`.

Quick sanity check:

```bash
test -x "$CMD_DIR/create_command"
test -x "$CMD_DIR/start_command"
test -x "$CMD_DIR/migrate_command"
test -f "$MODULE"
```

No output means OK.

Define a small wait helper so failures stop instead of hanging forever:

```bash
wait_log() {
  log_file="$1"
  text="$2"
  for _ in $(seq 1 200); do
    grep -q "$text" "$log_file" && return 0
    sleep 0.05
  done
  echo "Timed out waiting for: $text" >&2
  tail -n 40 "$log_file" >&2
  return 1
}
```

### 2. Start source server

```bash
touch "$RUN/source.ipc" "$RUN/source.log"
"$CMD_DIR/create_command" "$MODULE" \
  "$RUN/source.ipc" \
  "$RUN/source_main_memory.b" \
  "$RUN/source_checkpoint_memory.b" \
  - \
  "$RUN/source.log" > "$RUN/source_create.out"
cat "$RUN/source_create.out"
```

Expected output:

```text
server_pid=<number>
```

Wait until source is ready:

```bash
wait_log "$RUN/source.log" "request_server - wait for activation"
grep -E "started|pid|initializing|loading wasm|compiling|instantiation|wait for activation" "$RUN/source.log"
```

### 3. Activate source and checkpoint quickly

The default `3mm_with_cr.wasm` workload can finish before you type the next
command. Send `start_command` and `migrate_command` together:

```bash
"$CMD_DIR/start_command" "$RUN/source.ipc"
sleep 0.01
"$CMD_DIR/migrate_command" "$RUN/source.ipc"
wait_log "$RUN/source.log" "request_server - checkpoint completed"
grep -E "activated|restore memory|checkpoint|end of call" "$RUN/source.log"
ls -lh "$RUN"/source_*memory.b
```

You need both files:

```text
source_main_memory.b
source_checkpoint_memory.b
```

If source log reaches `request_server - end of call` without
`request_server - checkpoint completed`, the workload finished before migration
was requested. Start a fresh `RUN` and retry this block with a shorter sleep:

```bash
"$CMD_DIR/start_command" "$RUN/source.ipc"
sleep 0.001
"$CMD_DIR/migrate_command" "$RUN/source.ipc"
```

### 4. Package checkpoint state

```bash
mkdir -p "$RUN/state"
cp "$RUN/source_main_memory.b" "$RUN/state/main_memory.b"
cp "$RUN/source_checkpoint_memory.b" "$RUN/state/checkpoint_memory.b"
tar -C "$RUN/state" -czf "$RUN/wasm-state.tar.gz" main_memory.b checkpoint_memory.b
tar -tzf "$RUN/wasm-state.tar.gz"
```

Expected archive contents:

```text
main_memory.b
checkpoint_memory.b
```

### 5. Seed destination memory files

```bash
mkdir -p "$RUN/restored"
tar -C "$RUN/restored" -xzf "$RUN/wasm-state.tar.gz"
cp "$RUN/restored/main_memory.b" "$RUN/dest_main_memory.b"
cp "$RUN/restored/checkpoint_memory.b" "$RUN/dest_checkpoint_memory.b"
ls -lh "$RUN"/dest_*memory.b
```

### 6. Start destination server

```bash
touch "$RUN/dest.ipc" "$RUN/dest.log"
"$CMD_DIR/create_command" "$MODULE" \
  "$RUN/dest.ipc" \
  "$RUN/dest_main_memory.b" \
  "$RUN/dest_checkpoint_memory.b" \
  - \
  "$RUN/dest.log" > "$RUN/dest_create.out"
cat "$RUN/dest_create.out"
```

Wait until destination is ready:

```bash
wait_log "$RUN/dest.log" "request_server - wait for activation"
grep -E "started|pid|initializing|loading wasm|compiling|instantiation|wait for activation" "$RUN/dest.log"
```

### 7. Activate destination

```bash
"$CMD_DIR/start_command" "$RUN/dest.ipc"
wait_log "$RUN/dest.log" "request_server - end of call"
grep -E "activated|restore memory|checkpoint|end of call" "$RUN/dest.log"
```

Successful migration means:

- source log has `request_server - checkpoint completed`;
- source memory files exist;
- destination log has `request_server - restore memory completed`;
- destination log has `request_server - end of call`.

One-line final check:

```bash
grep -q "checkpoint completed" "$RUN/source.log" && \
grep -q "restore memory completed" "$RUN/dest.log" && \
grep -q "end of call" "$RUN/dest.log" && \
echo "manual migration OK"
```

## Common error

If `start_command` or `migrate_command` prints:

```text
Failed to open file (fd == -1)
: Permission denied
```

the IPC file is probably owned by another user because `create_command` or
`touch` was run with `sudo`. Delete that `manual-*` directory or create a fresh
one, then rerun the manual flow without `sudo`. The local `-` cgroup argument
does not need elevated privileges.

If `create_command | tee ...` prints `server_pid=...` and then appears stuck,
that is a pipe deadlock: the forked request server inherited stdout, so `tee`
keeps waiting. Use `> "$RUN/source_create.out"` and then `cat` the output file,
as shown above.

This command fails:

```bash
./create_command comp.wasm ipc_file.txt main_memory.b checkpoint_memory.b - log_file.txt
```

`comp.wasm` is only a placeholder in the upstream README. Use a real injected module path, for example:

```bash
./create_command ../wasm_test_computation/3mm_with_cr.wasm ipc_file.txt main_memory.b checkpoint_memory.b - log_file.txt
```

If you run from repo root, use the full path:

```bash
WASM-migration/wasm-migrate-commands/build/create_command \
  WASM-migration/wasm-migrate-commands/wasm_test_computation/3mm_with_cr.wasm \
  ipc_file.txt main_memory.b checkpoint_memory.b - log_file.txt
```

## Outputs

- Metrics CSV: `../metrics/migration_metrics.csv`
- Per-run logs and memory files: `artifacts/host/<run-id>/`
- Process snapshots from `/proc/<pid>`: `artifacts/host/<run-id>/process_snapshots.json`

The CSV uses the same columns as the CRIU experiments. For this local
proof-of-concept, `transfer_ms` is a host file copy, not network transfer.

## Useful flags

```bash
python3 WASM-migration/simplest-example/run_host_migration.py \
  --run-id my-test-001 \
  --warmup-seconds 0.01 \
  --timeout-seconds 15
```
