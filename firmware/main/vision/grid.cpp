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
#include <algorithm>
#include <array>
#include <cstring>
#include "vision/grid.h"
#include "config/config.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static std::array<uint32_t, GRID_COLS * GRID_ROWS> s_counts;
static uint32_t s_frames;
static int64_t s_started_us;
static uint16_t s_frame_width = GRID_DEFAULT_FRAME_WIDTH;
static uint16_t s_frame_height = GRID_DEFAULT_FRAME_HEIGHT;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

void grid_reset(uint16_t frame_width, uint16_t frame_height)
{
    taskENTER_CRITICAL(&s_lock);
    s_counts.fill(0);
    s_frames = 0;
    s_started_us = esp_timer_get_time();
    s_frame_width = frame_width;
    s_frame_height = frame_height;
    taskEXIT_CRITICAL(&s_lock);
}

void grid_clear()
{
    grid_reset(s_frame_width, s_frame_height);
}

static int cell_of_bottom_center(const box_t &box)
{
    int center_x = (box.x1 + box.x2) / 2;
    int bottom_y = box.y2;
    int x = std::clamp(center_x, 0, s_frame_width - 1);
    int y = std::clamp(bottom_y, 0, s_frame_height - 1);
    int row = y * GRID_ROWS / s_frame_height;
    int column = x * GRID_COLS / s_frame_width;
    return row * GRID_COLS + column;
}

void grid_add(const box_t *boxes, int count)
{
    taskENTER_CRITICAL(&s_lock);
    for (int index = 0; index < count; index++) {
        int cell = cell_of_bottom_center(boxes[index]);
        s_counts[cell]++;
    }
    s_frames++;
    taskEXIT_CRITICAL(&s_lock);
}

uint32_t grid_frames()
{
    return s_frames;
}

size_t grid_payload(uint8_t *buffer, size_t capacity)
{
    const size_t counts_bytes = s_counts.size() * sizeof(uint32_t);
    const size_t payload_bytes = GRID_HEADER_BYTES + counts_bytes;
    if (capacity < payload_bytes) {
        return 0;
    }
    taskENTER_CRITICAL(&s_lock);
    grid_header_t header = {};
    header.cols = GRID_COLS;
    header.rows = GRID_ROWS;
    header.frames = s_frames;
    header.elapsed_us = esp_timer_get_time() - s_started_us;
    header.frame_width = s_frame_width;
    header.frame_height = s_frame_height;
    memcpy(buffer, &header, sizeof(header));
    memcpy(buffer + GRID_HEADER_BYTES, s_counts.data(), counts_bytes);
    taskEXIT_CRITICAL(&s_lock);
    return payload_bytes;
}
