/* Heap-backed Game of Life workload for CRIU migration testing.
 *
 * The benchmark needs a process with meaningful resident memory.  The original
 * 50x20 stack grid was excellent as a smoke test, but it produced only a tiny
 * CRIU image.  This version allocates two large grids on the heap and prints a
 * compact heartbeat instead of dumping the full board to stdout.
 */

#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DEFAULT_WIDTH 2048
#define DEFAULT_HEIGHT 2048
#define DEFAULT_SEED 0xC0FFEEu
#define MAX_GRID_RENDER_CELLS 20000

static volatile sig_atomic_t running = 1;

typedef enum {
    OUTPUT_SUMMARY,
    OUTPUT_GRID,
} OutputMode;

typedef enum {
    PATTERN_RANDOM,
    PATTERN_CANNON,
} PatternMode;

static void signal_handler(int sig) {
    (void)sig;
    running = 0;
}

static size_t parse_size_env(const char *name, size_t fallback) {
    const char *raw = getenv(name);
    if (!raw || !*raw) {
        return fallback;
    }

    errno = 0;
    char *end = NULL;
    unsigned long value = strtoul(raw, &end, 10);
    if (errno != 0 || end == raw || *end != '\0' || value == 0) {
        fprintf(stderr, "Invalid %s=%s, using %zu\n", name, raw, fallback);
        return fallback;
    }
    return (size_t)value;
}

static uint32_t parse_seed_env(void) {
    const char *raw = getenv("GOL_SEED");
    if (!raw || !*raw) {
        return DEFAULT_SEED;
    }

    errno = 0;
    char *end = NULL;
    unsigned long value = strtoul(raw, &end, 0);
    if (errno != 0 || end == raw || *end != '\0') {
        fprintf(stderr, "Invalid GOL_SEED=%s, using %u\n", raw, DEFAULT_SEED);
        return DEFAULT_SEED;
    }
    return (uint32_t)value;
}

static OutputMode parse_output_mode(void) {
    const char *raw = getenv("GOL_OUTPUT_MODE");
    if (!raw || !*raw || strcmp(raw, "summary") == 0 || strcmp(raw, "heartbeat") == 0) {
        return OUTPUT_SUMMARY;
    }
    if (strcmp(raw, "grid") == 0 || strcmp(raw, "draw") == 0 || strcmp(raw, "full") == 0) {
        return OUTPUT_GRID;
    }

    fprintf(stderr, "Invalid GOL_OUTPUT_MODE=%s, using summary\n", raw);
    return OUTPUT_SUMMARY;
}

static PatternMode parse_pattern_mode(void) {
    const char *raw = getenv("GOL_PATTERN");
    if (!raw || !*raw || strcmp(raw, "random") == 0) {
        return PATTERN_RANDOM;
    }
    if (
        strcmp(raw, "cannon") == 0
        || strcmp(raw, "gosper") == 0
        || strcmp(raw, "glider-gun") == 0
        || strcmp(raw, "gosper-glider-gun") == 0
    ) {
        return PATTERN_CANNON;
    }

    fprintf(stderr, "Invalid GOL_PATTERN=%s, using random\n", raw);
    return PATTERN_RANDOM;
}

static uint32_t xorshift32(uint32_t x) {
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return x ? x : 0x9E3779B9u;
}

static uint32_t alive_value(size_t row, size_t col, unsigned long generation, uint32_t seed) {
    uint32_t x = seed;
    x ^= (uint32_t)(row * 2654435761u);
    x ^= (uint32_t)(col * 2246822519u);
    x ^= (uint32_t)(generation * 3266489917u);
    return xorshift32(x);
}

static void set_alive(
    uint32_t *grid,
    size_t width,
    size_t height,
    size_t row,
    size_t col,
    uint32_t seed
) {
    if (row < height && col < width) {
        grid[row * width + col] = alive_value(row, col, 0, seed);
    }
}

static void init_random_grid(uint32_t *grid, size_t width, size_t height, uint32_t seed) {
    for (size_t row = 0; row < height; row++) {
        for (size_t col = 0; col < width; col++) {
            uint32_t x = alive_value(row, col, 0, seed);
            grid[row * width + col] = (x % 100u < 35u) ? x : 0u;
        }
    }
}

static void init_cannon_grid(uint32_t *grid, size_t width, size_t height, uint32_t seed) {
    static const unsigned char cells[][2] = {
        {5, 1}, {5, 2}, {6, 1}, {6, 2},
        {3, 13}, {3, 14}, {4, 12}, {4, 16}, {5, 11}, {5, 17},
        {6, 11}, {6, 15}, {6, 17}, {6, 18}, {7, 11}, {7, 17},
        {8, 12}, {8, 16}, {9, 13}, {9, 14},
        {1, 25}, {2, 23}, {2, 25}, {3, 21}, {3, 22}, {4, 21},
        {4, 22}, {5, 21}, {5, 22}, {6, 23}, {6, 25}, {7, 25},
        {3, 35}, {3, 36}, {4, 35}, {4, 36},
    };

    memset(grid, 0, width * height * sizeof(uint32_t));
    if (width < 38 || height < 11) {
        fprintf(stderr, "GOL_PATTERN=cannon needs at least 38x11, using random\n");
        init_random_grid(grid, width, height, seed);
        return;
    }

    size_t row_offset = height > 11 ? (height - 11) / 2 : 0;
    size_t col_offset = width > 38 ? (width - 38) / 2 : 0;
    for (size_t i = 0; i < sizeof(cells) / sizeof(cells[0]); i++) {
        set_alive(
            grid,
            width,
            height,
            row_offset + cells[i][0],
            col_offset + cells[i][1],
            seed
        );
    }
}

static int count_neighbors(const uint32_t *grid, size_t width, size_t height, size_t row, size_t col) {
    int count = 0;
    for (int dr = -1; dr <= 1; dr++) {
        for (int dc = -1; dc <= 1; dc++) {
            if (dr == 0 && dc == 0) {
                continue;
            }

            long nr = (long)row + dr;
            long nc = (long)col + dc;
            if (nr >= 0 && nr < (long)height && nc >= 0 && nc < (long)width) {
                count += grid[(size_t)nr * width + (size_t)nc] != 0u;
            }
        }
    }
    return count;
}

static void update_grid(
    const uint32_t *current,
    uint32_t *next,
    size_t width,
    size_t height,
    unsigned long generation,
    uint32_t seed
) {
    for (size_t row = 0; row < height; row++) {
        for (size_t col = 0; col < width; col++) {
            size_t idx = row * width + col;
            int alive = current[idx] != 0u;
            int neighbors = count_neighbors(current, width, height, row, col);
            int next_alive = alive ? (neighbors == 2 || neighbors == 3) : (neighbors == 3);
            next[idx] = next_alive ? alive_value(row, col, generation + 1, seed) : 0u;
        }
    }
}

static void print_summary(unsigned long generation, const uint32_t *grid, size_t width, size_t height) {
    size_t alive = 0;
    uint32_t checksum = 2166136261u;
    size_t cells = width * height;

    for (size_t i = 0; i < cells; i++) {
        if (grid[i] != 0u) {
            alive++;
        }
        checksum ^= grid[i];
        checksum *= 16777619u;
    }

    printf(
        "generation=%lu width=%zu height=%zu alive=%zu checksum=%08x\n",
        generation,
        width,
        height,
        alive,
        checksum
    );
}

static void print_grid(unsigned long generation, const uint32_t *grid, size_t width, size_t height) {
    printf("=== Generation %lu (%zux%zu) ===\n", generation, width, height);
    for (size_t row = 0; row < height; row++) {
        for (size_t col = 0; col < width; col++) {
            putchar(grid[row * width + col] ? 'X' : '-');
        }
        putchar('\n');
    }
    putchar('\n');
}

int main(void) {
    size_t width = parse_size_env("GOL_WIDTH", DEFAULT_WIDTH);
    size_t height = parse_size_env("GOL_HEIGHT", DEFAULT_HEIGHT);
    uint32_t seed = parse_seed_env();
    OutputMode output_mode = parse_output_mode();
    PatternMode pattern_mode = parse_pattern_mode();

    if (height != 0 && width > SIZE_MAX / height) {
        fprintf(stderr, "Grid dimensions overflow: %zux%zu\n", width, height);
        return 1;
    }

    size_t cells = width * height;
    if (cells > SIZE_MAX / sizeof(uint32_t)) {
        fprintf(stderr, "Grid allocation overflow: %zu cells\n", cells);
        return 1;
    }

    size_t grid_bytes = cells * sizeof(uint32_t);
    if (output_mode == OUTPUT_GRID && cells > MAX_GRID_RENDER_CELLS) {
        fprintf(
            stderr,
            "GOL_OUTPUT_MODE=grid requested for %zu cells; using summary to avoid huge stdout\n",
            cells
        );
        output_mode = OUTPUT_SUMMARY;
    }

    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);
    setvbuf(stdout, NULL, _IOLBF, 0);

    uint32_t *current = malloc(grid_bytes);
    uint32_t *next = malloc(grid_bytes);
    if (!current || !next) {
        fprintf(stderr, "Failed to allocate two %zu-byte grids\n", grid_bytes);
        free(current);
        free(next);
        return 1;
    }

    if (pattern_mode == PATTERN_CANNON) {
        init_cannon_grid(current, width, height, seed);
    } else {
        init_random_grid(current, width, height, seed);
    }
    memset(next, 0, grid_bytes);

    printf(
        "gol_start width=%zu height=%zu cells=%zu heap_bytes=%zu seed=%u output_mode=%s pattern=%s\n",
        width,
        height,
        cells,
        grid_bytes * 2,
        seed,
        output_mode == OUTPUT_GRID ? "grid" : "summary",
        pattern_mode == PATTERN_CANNON ? "cannon" : "random"
    );

    unsigned long generation = 0;
    while (running) {
        if (output_mode == OUTPUT_GRID) {
            print_grid(generation, current, width, height);
        } else {
            print_summary(generation, current, width, height);
        }
        update_grid(current, next, width, height, generation, seed);
        memcpy(current, next, grid_bytes);
        generation++;
        sleep(1);
    }

    free(current);
    free(next);
    return 0;
}
