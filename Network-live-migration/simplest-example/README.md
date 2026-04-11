# Simplest CRIU Migration Example (Same Host)

This folder contains a minimal demonstration of process checkpoint and restore using CRIU on the same machine.

The goal of this test was to validate the migration flow locally before moving to multi-node/container scenarios.

## What Was Done

1. Created the source file `tcp-howto.c` .
2. Wrote a simple TCP server and client program.
3. Compiled the program with GCC.
4. Started the server and verified it was running.
5. Started the client and verified it was connected to the server.
6. Retrieved its PID with `ps aux | grep tcp-howto`.
7. Created a `img-dir/` directory to store CRIU image files.
8. Ran CRIU dump to checkpoint the running process with the --tcp-established option (this is a must, since client have active TCP connection and we should explicitly inform crtools about it).
9. Ran CRIU restore from the generated checkpoint images and check there was no new connection created in the server.

## Program Used

<details>
  <summary>Click to expand</summary>

```c
#include <sys/socket.h>
#include <linux/types.h>
#include <sys/types.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <signal.h>

static int serve_new_conn(int sk)
{
	int rd, wr;
	char buf[1024];

	printf("New connection\n");

	while (1) {
		rd = read(sk, buf, sizeof(buf));
		if (!rd)
			break;

		if (rd < 0) {
			perror("Can't read socket");
			return 1;
		}

		wr = 0;
		while (wr < rd) {
			int w;

			w = write(sk, buf + wr, rd - wr);
			if (w <= 0) {
				perror("Can't write socket");
				return 1;
			}

			wr += w;
		}
	}

	printf("Done\n");
	return 0;
}

static int main_srv(int argc, char **argv)
{
	int sk, port, ret;
	struct sockaddr_in addr;

	/*
	 * Let kids die themselves
	 */

	signal(SIGCHLD, SIG_IGN);

	sk = socket(PF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (sk < 0) {
		perror("Can't create socket");
		return -1;
	}

	port = atoi(argv[1]);
	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	addr.sin_addr.s_addr = htonl(INADDR_ANY);
	addr.sin_port = htons(port);

	printf("Binding to port %d\n", port);

	ret = bind(sk, (struct sockaddr *)&addr, sizeof(addr));
	if (ret < 0) {
		perror("Can't bind socket");
		return -1;
	}

	ret = listen(sk, 16);
	if (ret < 0) {
		perror("Can't put sock to listen");
		return -1;
	}

	printf("Waiting for connections\n");
	while (1) {
		int ask, pid;

		ask = accept(sk, NULL, NULL);
		if (ask < 0) {
			perror("Can't accept new conn");
			return -1;
		}

		pid = fork();
		if (pid < 0) {
			perror("Can't fork");
			return -1;
		}

		if (pid > 0)
			close(ask);
		else {
			close(sk);
			ret = serve_new_conn(ask);
			exit(ret);
		}
	}
}

static int main_cl(int argc, char **argv)
{
	int sk, port, ret, val = 1, rval;
	struct sockaddr_in addr;

	sk = socket(PF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (sk < 0) {
		perror("Can't create socket");
		return -1;
	}

	port = atoi(argv[2]);
	printf("Connecting to %s:%d\n", argv[1], port);
	memset(&addr, 0, sizeof(addr));
	addr.sin_family = AF_INET;
	ret = inet_aton(argv[1], &addr.sin_addr);
	if (ret < 0) {
		perror("Can't convert addr");
		return -1;
	}
	addr.sin_port = htons(port);

	ret = connect(sk, (struct sockaddr *)&addr, sizeof(addr));
	if (ret < 0) {
		perror("Can't connect");
		return -1;
	}

	while (1) {
		write(sk, &val, sizeof(val));
		rval = -1;
		read(sk, &rval, sizeof(rval));
		printf("PP %d -> %d\n", val, rval);
		sleep(2);
		val++;
	}
}

int main(int argc, char **argv)
{
	if (argc == 2)
		return main_srv(argc, argv);
	else if (argc == 3)
		return main_cl(argc, argv);

	printf("Bad usage\n");
	return 1;
}
```

</details>

## Commands Executed
	Terminal 1:
	```bash
	gcc tcp-howto.c
	./tcp-howto 5000
	```
	Then, in Terminal 2, open a client connection:
	```bash
	./tcp-howto 127.0.0.1 5000
	```
	Terminal 3:
	```bash
    mkdir img-dir
    ps aux | grep tcp-howto
    # note the PID of the server process (e.g., 12345)
    criu dump --tree 12345 --images-dir img-dir/ -v4 -o dump.log --shell-job --tcp-established
    # Then restore from the generated images
    sudo criu restore --images-dir img-dir/ -v4 -o rst.log --shell-job --tcp-established
	```

## Result

- The process was checkpointed and restored successfully on the same host a connection to the server without creating a new one.
- After restore, output continued from the restored process state .

This confirms the basic CRIU checkpoint/restore workflow in a controlled local environment and with network connections (TCP).
