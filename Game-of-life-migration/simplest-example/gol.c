/* Game of Life application for CRIU migration testing.
 * Prints an evolving grid every second to stdout.
 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>

/* IMPORTANT: If you change the size of the drawing below, 
 * you MUST update these WIDTH and HEIGHT macros to match! */
#define WIDTH 50
#define HEIGHT 20

const char *initial_pattern[HEIGHT] = {
    "--------------------------------------------------",
    "--------------------------------------------------",
    "--------------------------X-----------------------",
    "------------------------X-X-----------------------",
    "--------------XX------XX------------XX------------",
    "-------------X---X----XX------------XX------------",
    "--XX--------X-----X---XX--------------------------",
    "--XX--------X---X-XX----X-X-----------------------",
    "------------X-----X-------X-----------------------",
    "-------------X---X--------------------------------",
    "--------------XX----------------------------------",
    "--------------------------------------------------",
    "--------------------------------------------------",
    "--------------------------------------------------",
    "--------------------------------------------------",
    "--------------------------------------------------",
    "--------------------------------------------------",
    "--------------------------------------------------",
    "--------------------------------------------------",
    "--------------------------------------------------"
};

static volatile sig_atomic_t running = 1;

static void signal_handler(int sig) {
    (void)sig;
    running = 0;
}

/* Fills the grid based on the hardcoded initial_pattern array */
void init_grid(int grid[HEIGHT][WIDTH]) {
    for (int i = 0; i < HEIGHT; i++) {
        for (int j = 0; j < WIDTH; j++) {
            /* 'X' becomes a 1 (alive), everything else becomes a 0 (dead) */
            if (initial_pattern[i][j] == 'X') {
                grid[i][j] = 1;
            } else {
                grid[i][j] = 0;
            }
        }
    }
}

/* Prints the grid state to stdout using the same X and - notation */
void print_grid(unsigned long generation, int grid[HEIGHT][WIDTH]) {
    printf("=== Generation %lu ===\n", generation);
    for (int i = 0; i < HEIGHT; i++) {
        for (int j = 0; j < WIDTH; j++) {
            printf("%c", grid[i][j] ? 'X' : '-');
        }
        printf("\n");
    }
    printf("\n"); /* Extra newline for spacing between frames */
}

/* Counts alive neighbors for cell (r, c) */
int count_neighbors(int grid[HEIGHT][WIDTH], int r, int c) {
    int count = 0;
    for (int i = -1; i <= 1; i++) {
        for (int j = -1; j <= 1; j++) {
            if (i == 0 && j == 0) continue;
            
            int nr = r + i;
            int nc = c + j;
            
            /* Check boundaries to avoid out-of-bounds array access */
            if (nr >= 0 && nr < HEIGHT && nc >= 0 && nc < WIDTH) {
                count += grid[nr][nc];
            }
        }
    }
    return count;
}

/* Applies Conway's rules to generate the next state */
void update_grid(int current[HEIGHT][WIDTH], int next[HEIGHT][WIDTH]) {
    for (int i = 0; i < HEIGHT; i++) {
        for (int j = 0; j < WIDTH; j++) {
            int neighbors = count_neighbors(current, i, j);
            
            if (current[i][j] == 1) {
                next[i][j] = (neighbors == 2 || neighbors == 3) ? 1 : 0;
            } else {
                next[i][j] = (neighbors == 3) ? 1 : 0;
            }
        }
    }
}

/* Copies the calculated next state into the current state */
void copy_grid(int dest[HEIGHT][WIDTH], int src[HEIGHT][WIDTH]) {
    for (int i = 0; i < HEIGHT; i++) {
        for (int j = 0; j < WIDTH; j++) {
            dest[i][j] = src[i][j];
        }
    }
}

int main(void) {
    int current[HEIGHT][WIDTH];
    int next[HEIGHT][WIDTH];
    unsigned long generation = 0;

    /* Set up signal handlers for graceful shutdown */
    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);

    /* Ensure each line is flushed promptly */
    setvbuf(stdout, NULL, _IOLBF, 0);

    /* Initialize the grid from the string array */
    init_grid(current);

    /* Main loop: print, calculate next state, and sleep */
    while (running) {
        print_grid(generation, current);
        update_grid(current, next);
        copy_grid(current, next);
        
        generation++;
        sleep(1);
    }

    return 0;
}