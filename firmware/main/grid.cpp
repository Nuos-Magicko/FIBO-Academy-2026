// Presence intensity (sampled): +1 per box per processed frame in the cell holding the box's
// bottom-center point. Not foot traffic; normalize by frames or elapsed time on the PC.
#include <cstring>
#include "app.h"
#include "esp_timer.h"
#include "freertos/semphr.h"

static uint32_t s_counts[GRID_COLS * GRID_ROWS];
static uint32_t s_frames;
static int64_t s_start_us;
static uint16_t s_w = 320, s_h = 240;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

void grid_reset(uint16_t w, uint16_t h)
{
    taskENTER_CRITICAL(&s_lock);
    memset(s_counts, 0, sizeof s_counts);
    s_frames = 0;
    s_start_us = esp_timer_get_time();
    s_w = w;
    s_h = h;
    taskEXIT_CRITICAL(&s_lock);
}

void grid_clear() { grid_reset(s_w, s_h); }

void grid_add(const box_t *b, int n)
{
    taskENTER_CRITICAL(&s_lock);
    for (int i = 0; i < n; i++) {
        int cx = (b[i].x1 + b[i].x2) / 2, cy = b[i].y2;
        cx = cx < 0 ? 0 : cx >= s_w ? s_w - 1 : cx;
        cy = cy < 0 ? 0 : cy >= s_h ? s_h - 1 : cy;
        s_counts[(cy * GRID_ROWS / s_h) * GRID_COLS + cx * GRID_COLS / s_w]++;
    }
    s_frames++;
    taskEXIT_CRITICAL(&s_lock);
}

uint32_t grid_frames() { return s_frames; }

// cols u16, rows u16, frames u32, elapsed_us i64, frame_w u16, frame_h u16, counts u32[cols*rows] (row-major)
size_t grid_payload(uint8_t *buf, size_t cap)
{
    const size_t need = 20 + sizeof s_counts;
    if (cap < need) return 0;
    uint16_t cols = GRID_COLS, rows = GRID_ROWS;
    taskENTER_CRITICAL(&s_lock);
    int64_t elapsed = esp_timer_get_time() - s_start_us;
    memcpy(buf, &cols, 2);
    memcpy(buf + 2, &rows, 2);
    memcpy(buf + 4, &s_frames, 4);
    memcpy(buf + 8, &elapsed, 8);
    memcpy(buf + 16, &s_w, 2);
    memcpy(buf + 18, &s_h, 2);
    memcpy(buf + 20, s_counts, sizeof s_counts);
    taskEXIT_CRITICAL(&s_lock);
    return need;
}
