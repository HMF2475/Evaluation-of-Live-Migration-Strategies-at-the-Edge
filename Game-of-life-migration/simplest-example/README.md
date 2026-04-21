
# Simplest CRIU Migration Example (Same Host)

This folder contains a minimal demonstration of process checkpoint and restore using CRIU on the same machine, using a **Game of Life** C application as the workload.

The goal of this test is to validate the migration flow locally before moving to multi-node/container scenarios.

## What Was Done

1. Created the source file `gol.c` (a simple Conway's Game of Life implementation).
2. Compiled the program with GCC.
3. Started the program and verified it was running (it prints an evolving grid every second).
4. Retrieved its PID with `pidof gol`.
5. Created a `checkpoint/` directory to store CRIU image files.
6. Ran CRIU dump to checkpoint the running process.
7. Ran CRIU restore from the generated checkpoint images.

## Program Used

See `gol.c` in this folder. It prints a 50x20 grid, updating every second, with a hardcoded initial pattern.

## Commands Executed

Terminal 1:
```bash
gcc gol.c -o gol
./gol
pidof gol
mkdir checkpoint
```

Then, in Terminal 1, after noting the PID (e.g., XXXX):
```bash
sudo criu dump -t XXXX -D checkpoint/ -j -v4 --shell-job
```
Terminal 2:
```bash
sudo criu restore -D checkpoint/ -j
```

## Result

- The Game of Life process was checkpointed and restored successfully on the same host.
- After restore, the evolving grid continued from the restored process state.

This confirms the basic CRIU checkpoint/restore workflow in a controlled local environment using a non-trivial application.
