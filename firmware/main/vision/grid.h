/**
 * @file grid.h
 * The presence heatmap counted on the board (see grid.cpp).
 */
#pragma once
#include <cstddef>
#include <cstdint>
#include "vision/bundle.h"

/** Clears the presence grid and sets the frame size its cells are mapped onto. */
void grid_reset(uint16_t frame_width, uint16_t frame_height);
/** Clears the counts and keeps the frame size. */
void grid_clear();
/** Adds one processed frame: +1 in the cell holding each box's bottom-center point. */
void grid_add(const box_t *boxes, int count);
/**
 * Writes the PRESENCE_GRID payload: cols u16, rows u16, frames u32, elapsed_us i64,
 * frame_width u16, frame_height u16, then cols x rows u32 counts, row-major. Returns its length.
 */
size_t grid_payload(uint8_t *buffer, size_t capacity);
uint32_t grid_frames();
