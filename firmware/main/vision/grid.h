/**
 * @file grid.h
 * The presence heatmap counted on the board (see grid.cpp).
 */
#pragma once
#include <cstddef>
#include <cstdint>
#include "config/config.h"
#include "vision/bundle.h"

/** The start of a PRESENCE_GRID payload; GRID_COLS x GRID_ROWS u32 counts follow, row-major. */
struct __attribute__((packed)) grid_header_t {
    uint16_t cols;
    uint16_t rows;
    uint32_t frames;
    int64_t elapsed_us;
    uint16_t frame_width;
    uint16_t frame_height;
};
constexpr size_t GRID_HEADER_BYTES = 20;
static_assert(sizeof(grid_header_t) == GRID_HEADER_BYTES, "grid header layout must match docs/protocol.md");
/** The frame size the grid assumes until it knows the real one (QVGA). */
constexpr uint16_t GRID_DEFAULT_FRAME_WIDTH = 320;
constexpr uint16_t GRID_DEFAULT_FRAME_HEIGHT = 240;
constexpr size_t GRID_PAYLOAD_BYTES = GRID_HEADER_BYTES + GRID_COLS * GRID_ROWS * sizeof(uint32_t);

/** Clears the presence grid and sets the frame size its cells are mapped onto. */
void grid_reset(uint16_t frame_width, uint16_t frame_height);
/** Clears the counts and keeps the frame size. */
void grid_clear();
/** Adds one processed frame: +1 in the cell holding each box's bottom-center point. */
void grid_add(const box_t *boxes, int count);
/** Writes the PRESENCE_GRID payload (header + counts) and returns its length, or 0 if it does not fit. */
size_t grid_payload(uint8_t *buffer, size_t capacity);
uint32_t grid_frames();
