/**
 * @file grid.cpp
 * The presence heatmap: where people were.
 *
 * The picture is divided into 20 x 15 cells. For every processed frame, each detected person
 * adds +1 to the cell under their feet (the middle of the box's bottom edge). A person who
 * stands still adds +1 in every frame, so this shows where people spent time, not how many
 * people walked past. The laptop divides the counts by the number of frames or seconds so that
 * runs of different length can be compared.
 */
#include <cstring>
#include "vision/grid.h"
#include "config/config.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static constexpr size_t GRID_HEADER_BYTES = 20;

static uint32_t s_counts[GRID_COLS * GRID_ROWS];
static uint32_t s_frames;
static int64_t s_started_us;
static uint16_t s_frame_width = 320, s_frame_height = 240;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

void grid_reset(uint16_t frame_width, uint16_t frame_height)
{
    taskENTER_CRITICAL(&s_lock);
    memset(s_counts, 0, sizeof s_counts);
    s_frames = 0;
    s_started_us = esp_timer_get_time();
    s_frame_width = frame_width;
    s_frame_height = frame_height;
    taskEXIT_CRITICAL(&s_lock);
}

void grid_clear() { grid_reset(s_frame_width, s_frame_height); }

static int clamp(int value, int lo, int hi) { return value < lo ? lo : value > hi ? hi : value; }

static int cell_of_bottom_center(const box_t &box)
{
    int x = clamp((box.x1 + box.x2) / 2, 0, s_frame_width - 1);
    int y = clamp(box.y2, 0, s_frame_height - 1);
    return (y * GRID_ROWS / s_frame_height) * GRID_COLS + x * GRID_COLS / s_frame_width;
}

void grid_add(const box_t *boxes, int count)
{
    taskENTER_CRITICAL(&s_lock);
    for (int i = 0; i < count; i++) s_counts[cell_of_bottom_center(boxes[i])]++;
    s_frames++;
    taskEXIT_CRITICAL(&s_lock);
}

uint32_t grid_frames() { return s_frames; }

size_t grid_payload(uint8_t *buffer, size_t capacity)
{
    const size_t payload_bytes = GRID_HEADER_BYTES + sizeof s_counts;
    if (capacity < payload_bytes) return 0;
    const uint16_t cols = GRID_COLS, rows = GRID_ROWS;
    taskENTER_CRITICAL(&s_lock);
    int64_t elapsed_us = esp_timer_get_time() - s_started_us;
    memcpy(buffer, &cols, 2);
    memcpy(buffer + 2, &rows, 2);
    memcpy(buffer + 4, &s_frames, 4);
    memcpy(buffer + 8, &elapsed_us, 8);
    memcpy(buffer + 16, &s_frame_width, 2);
    memcpy(buffer + 18, &s_frame_height, 2);
    memcpy(buffer + GRID_HEADER_BYTES, s_counts, sizeof s_counts);
    taskEXIT_CRITICAL(&s_lock);
    return payload_bytes;
}
