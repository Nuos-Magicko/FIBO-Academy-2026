/**
 * @file bundle.h
 * A "bundle" is one finished frame on its way to the laptop: the picture plus its boxes.
 *
 * Bundles come from a fixed pool made once at start-up. The vision task takes a free one, fills
 * it and puts it in the ready queue; the net task sends it and gives it back to the free queue.
 */
#pragma once
#include <cstdint>
#include "config/config.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "telemetry/metrics.h"

/** One detected person: the box corners in pixels and the AI's score (0..1). */
struct box_t {
    int16_t x1, y1, x2, y2;
    float score;
};

struct bundle_t {
    uint32_t frame_id;
    /** esp_timer time when esp_camera_fb_get() returned. */
    int64_t t_capture_us;
    /** Coordinate space of the boxes: the camera frame, or the embedded still. */
    uint16_t width, height;
    /** 0 when the bundle carries no JPEG (metadata modes). */
    uint32_t jpeg_len;
    uint8_t box_count;
    box_t boxes[MAX_BOXES_PER_FRAME];
    /** Per-frame stage times, sent as FRAME_TIMING when metrics_verbose is on. */
    int32_t stage_us[ST_COUNT];
    /** PSRAM buffer of JPEG_BUFFER_BYTES. */
    uint8_t *jpeg_buffer;
};

extern QueueHandle_t g_free_bundles, g_ready_bundles;
/** Allocates every bundle once and puts them all in the free queue. */
void pool_init();
