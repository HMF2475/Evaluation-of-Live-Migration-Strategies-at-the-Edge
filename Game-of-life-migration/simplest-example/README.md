# Simplest CRIU Migration Example (Same Host)

This folder contains a minimal demonstration of process checkpoint and restore using CRIU on the same machine.

The goal of this test was to validate the migration flow locally before moving to multi-node/container scenarios.

## What Was Done

1. Created the source file `simple_loop.c` .
2. Wrote a simple infinite loop program that increments and prints a gol every second.
3. Compiled the program with GCC.
4. Started the program and verified it was running.
5. Retrieved its PID with `pidof a.out`.
6. Created a `checkpoint/` directory to store CRIU image files.
7. Ran CRIU dump to checkpoint the running process.
8. Ran CRIU restore from the generated checkpoint images.

## Program Used

	#include <stdio.h>
	#include <unistd.h>

	int main()
	{
		long long i = 0;
		while(1) {
			printf("%lld\n", ++i);
			sleep(1);
		}
		return 0;
	}

## Commands Executed
	Terminal 1:
	```bash
	gcc simple_loop.c
	./a.out
	pidof a.out
	mkdir checkpoint
	```
	Then, in Terminal 1, after noting the PID (e.g., XXXX):
	```bash
	sudo criu dump -t XXXX -D checkpoint/ -j -v4
	```
	Terminal 2:
	```bash
	sudo criu restore -D checkpoint/ -j
	```

## Result

- The process was checkpointed and restored successfully on the same host.
- After restore, output continued from the restored process state .

This confirms the basic CRIU checkpoint/restore workflow in a controlled local environment.
