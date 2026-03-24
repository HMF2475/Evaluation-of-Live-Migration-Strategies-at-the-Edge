/* Simple counter application for CRIU migration testing.
 * Increments a counter every second and writes to stdout + logfile.
 * Usage: counter [logfile]
 * Default logfile: /home/ubuntu/counter.log
 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>

static volatile int running = 1;
static FILE *logfile = NULL;

void signal_handler(int sig) {
    running = 0;
}

int main(int argc, char *argv[]) {
    const char *logpath = "/home/ubuntu/counter.log";
    unsigned long counter = 0;

    if (argc > 1) {
        logpath = argv[1];
    }

    /* Open logfile for writing */
    logfile = fopen(logpath, "w");
    if (!logfile) {
        perror("fopen");
        return 1;
    }

    /* Redirect stdout to the same logfile */
    if (dup2(fileno(logfile), STDOUT_FILENO) < 0) {
        perror("dup2");
        fclose(logfile);
        return 1;
    }

    /* Set up signal handlers for graceful shutdown */
    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);

    /* Main loop: increment and print every second */
    while (running) {
        printf("%lu\n", counter);
        fflush(stdout);
        counter++;
        sleep(1);
    }

    fclose(logfile);
    return 0;
}
