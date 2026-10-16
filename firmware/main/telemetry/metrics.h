/**
 * @file metrics.h
 * What the board measures about itself: the counters since boot and the timing of every step of
 * a frame, summarized once per second as a METRICS record.
 */
#pragma once
#include <atomic>
#include <cstdint>
#include "cJSON.h"

/** Counters since boot. Field names are the keys of METRICS.counters on the wire. */
struct counters_t {
    std::atomic<uint32_t> cap_ok, cap_timeout, no_eoi, no_soi, fb_ovf, decode_fail, oversize;
    std::atomic<uint32_t> frames_processed, cadence_skipped, still_runs;
    std::atomic<uint32_t> bundles_sent, tx_dropped, no_client_discards, events_dropped;
    std::atomic<uint32_t> clients, cmd_ok, cmd_err, rx_crc_errors, rx_oversize, wifi_disconnects;
    std::atomic<uint64_t> bytes_sent;
};
extern counters_t g_counters;

/**
 * The steps of one frame that the board times, in microseconds. Their names on the laptop start
 * with "t_" (t_wait_frame, t_decode, ...); docs/experiments.md, section 1, explains each one.
 */
enum stage_t {
    /** Blocked in esp_camera_fb_get(). */
    ST_WAIT_FRAME,
    /** JPEG to RGB888. */
    ST_DECODE,
    /** Detector preprocess: resize and quantize to 224x224. */
    ST_PRE,
    /** The neural network only. */
    ST_INFER,
    /** Detector postprocess: decode boxes, NMS. */
    ST_POST,
    ST_GRID,
    /** JPEG copy into the transmit pool. */
    ST_COPY,
    /** Hand-off to the network queue, including any wait. */
    ST_ENQUEUE,
    /** From esp_camera_fb_get() returning to the bundle being queued: device processing latency. */
    ST_PROC,
    /** Net task: writing one bundle to the socket. */
    ST_SEND,
    ST_COUNT
};
extern const char *const STAGE_NAMES[ST_COUNT];

/** Value stored for a stage that was not measured for this frame. */
constexpr int32_t STAGE_NOT_MEASURED = -1;

void metrics_init();
void metrics_add(stage_t stage, int64_t duration_us);
/** Snapshots and resets the current window. The caller owns the returned JSON. */
cJSON *metrics_window();
