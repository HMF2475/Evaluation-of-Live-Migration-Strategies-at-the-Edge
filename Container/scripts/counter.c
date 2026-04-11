/* Simple counter application for CRIU migration testing.
 * Prints an incrementing integer every second to stdout.
 *
 * IMPORTANT:
 * - The migration framework redirects stdout to `/home/ubuntu/counter.out`.
 * - We avoid opening/writing a dedicated log file inside the process so we
 *   don't need to transfer log file contents during migration (which caused
 *   duplicated values when analyzing outputs from both nodes).
 */

#include <stdio.h>
#include <unistd.h>
#include <signal.h>

static volatile sig_atomic_t running = 1;

static void signal_handler(int sig) {
    (void)sig;
    running = 0;
}

int main(void) {
    unsigned long counter = 0;

    /* Set up signal handlers for graceful shutdown */
    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);

    /* Ensure each line is flushed promptly (useful for tail -f). */
    setvbuf(stdout, NULL, _IOLBF, 0);

    /* Main loop: increment and print every second */
    while (running) {
        printf("%lu\n", counter);
        counter++;
        sleep(1);
    }

    return 0;
}
