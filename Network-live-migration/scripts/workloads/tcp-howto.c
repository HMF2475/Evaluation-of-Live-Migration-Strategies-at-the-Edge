#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

static int serve_conn(int sk) {
  int32_t val = 0;
  while (1) {
    ssize_t rd = read(sk, &val, sizeof(val));
    if (rd == 0) {
      fprintf(stdout, "Client closed connection\n");
      fflush(stdout);
      return 0;
    }
    if (rd < 0) {
      perror("read");
      return 1;
    }
    if (rd != (ssize_t)sizeof(val)) {
      fprintf(stdout, "Short read: %zd bytes\n", rd);
      fflush(stdout);
      return 1;
    }

    ssize_t wr = 0;
    while (wr < (ssize_t)sizeof(val)) {
      ssize_t w = write(sk, ((char *)&val) + wr, sizeof(val) - wr);
      if (w <= 0) {
        perror("write");
        return 1;
      }
      wr += w;
    }
  }
}

static int main_srv(int argc, char **argv) {
  int sk, port, ret;
  struct sockaddr_in addr;

  signal(SIGCHLD, SIG_IGN);

  sk = socket(PF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (sk < 0) {
    perror("socket");
    return 1;
  }

  int one = 1;
  setsockopt(sk, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

  port = atoi(argv[1]);
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  addr.sin_port = htons((uint16_t)port);

  fprintf(stdout, "Binding to port %d\n", port);
  fflush(stdout);

  ret = bind(sk, (struct sockaddr *)&addr, sizeof(addr));
  if (ret < 0) {
    perror("bind");
    return 1;
  }

  ret = listen(sk, 16);
  if (ret < 0) {
    perror("listen");
    return 1;
  }

  fprintf(stdout, "Waiting for connections\n");
  fflush(stdout);

  while (1) {
    struct sockaddr_in peer;
    socklen_t peerlen = sizeof(peer);
    int ask = accept(sk, (struct sockaddr *)&peer, &peerlen);
    if (ask < 0) {
      perror("accept");
      return 1;
    }
    char peer_ip[64];
    inet_ntop(AF_INET, &peer.sin_addr, peer_ip, sizeof(peer_ip));
    fprintf(stdout, "New connection from %s:%d\n", peer_ip, ntohs(peer.sin_port));
    fflush(stdout);

    int pid = fork();
    if (pid < 0) {
      perror("fork");
      close(ask);
      return 1;
    }
    if (pid > 0) {
      close(ask);
      continue;
    }

    close(sk);
    int rc = serve_conn(ask);
    close(ask);
    exit(rc);
  }
}

static int bind_local_ip(int sk, const char *local_ip) {
  if (!local_ip || !*local_ip) {
    return 0;
  }

  struct sockaddr_in local;
  memset(&local, 0, sizeof(local));
  local.sin_family = AF_INET;
  local.sin_port = htons(0);
  if (inet_aton(local_ip, &local.sin_addr) == 0) {
    fprintf(stderr, "Invalid LOCAL_IP: %s\n", local_ip);
    return 1;
  }

  if (bind(sk, (struct sockaddr *)&local, sizeof(local)) < 0) {
    fprintf(stderr, "bind(LOCAL_IP=%s) failed: %s\n", local_ip, strerror(errno));
    return 1;
  }
  return 0;
}

static int main_cl(int argc, char **argv) {
  int sk, port, ret;
  struct sockaddr_in addr;

  sk = socket(PF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (sk < 0) {
    perror("socket");
    return 1;
  }

  const char *local_ip = getenv("LOCAL_IP");
  if (bind_local_ip(sk, local_ip) != 0) {
    close(sk);
    return 1;
  }

  port = atoi(argv[2]);
  fprintf(stdout, "Connecting to %s:%d\n", argv[1], port);
  if (local_ip && *local_ip) {
    fprintf(stdout, "Binding LOCAL_IP=%s\n", local_ip);
  }
  fflush(stdout);

  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  ret = inet_aton(argv[1], &addr.sin_addr);
  if (ret == 0) {
    fprintf(stderr, "Bad server IP: %s\n", argv[1]);
    close(sk);
    return 1;
  }
  addr.sin_port = htons((uint16_t)port);

  ret = connect(sk, (struct sockaddr *)&addr, sizeof(addr));
  if (ret < 0) {
    perror("connect");
    close(sk);
    return 1;
  }

  int32_t val = 1;
  while (1) {
    int32_t sendv = htonl(val);
    ssize_t wr = write(sk, &sendv, sizeof(sendv));
    if (wr != (ssize_t)sizeof(sendv)) {
      perror("write");
      return 1;
    }

    int32_t recvv = 0;
    ssize_t rd = read(sk, &recvv, sizeof(recvv));
    if (rd == 0) {
      fprintf(stdout, "Server closed connection\n");
      fflush(stdout);
      return 0;
    }
    if (rd != (ssize_t)sizeof(recvv)) {
      perror("read");
      return 1;
    }

    int32_t rval = ntohl(recvv);
    fprintf(stdout, "PP %d -> %d\n", val, rval);
    fflush(stdout);
    sleep(2);
    val++;
  }
}

int main(int argc, char **argv) {
  if (argc == 2) {
    return main_srv(argc, argv);
  }
  if (argc == 3) {
    return main_cl(argc, argv);
  }
  fprintf(stderr, "Usage:\n  %s <port>                  # server\n  %s <server_ip> <port>      # client\n", argv[0], argv[0]);
  return 1;
}

