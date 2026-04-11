# Simplest CRIU Migration Example (Same Host)

This folder is a local sanity check for CRIU dump/restore with an established TCP connection on one machine.

It is not the benchmark path. The benchmark workflow is documented in `Network-live-migration/TCP-live-migration.md`.

## Purpose

- Verify CRIU can dump/restore a TCP-connected process in a controlled environment.
- Validate basic flags (`--tcp-established`, `--shell-job`) before multi-node runs.

## Files

- `tcp-howto.c`: minimal TCP server/client test program.
- `img-dir/`: CRIU image output directory (created during the test).
- `tcp-howto`: compiled binary (created during the test).

## Quick Run

```bash
# Terminal 1: server
gcc tcp-howto.c -o tcp-howto
./tcp-howto 5000

# Terminal 2: client
./tcp-howto 127.0.0.1 5000

# Terminal 3: checkpoint + restore (replace PID with the client PID)
mkdir img-dir
criu dump --tree <PID> --images-dir img-dir -v4 -o dump.log --shell-job --tcp-established
sudo criu restore --images-dir img-dir -v4 -o restore.log --shell-job --tcp-established
```

## Expected Outcome

- Dump and restore complete without fatal errors.
- Client output continues after restore.
- Server does not accept an additional new connection for that client flow.
