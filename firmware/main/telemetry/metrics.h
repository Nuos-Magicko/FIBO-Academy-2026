/**
 * @file metrics.h
 * What the board measures about itself: the counters since boot and the timing of every step of
 * a frame, summarized once per second as a METRICS record.
 */
#pragma once
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include "cJSON.h"

/** Counters since boot. Field names are the keys of METRICS.counters on the wire. */
struct counters_t {
    std::atomic<uint32_t> cap_ok;
    std::atomic<uint32_t> cap_timeout;
    std::atomic<uint32_t> no_eoi;
    std::atomic<uint32_t> no_soi;
    std::atomic<uint32_t> fb_ovf;
    std::atomic<uint32_t> decode_fail;
    std::atomic<uint32_t> oversize;
    std::atomic<uint32_t> frames_processed;
    std::atomic<uint32_t> cadence_skipped;
    std::atomic<uint32_t> still_runs;
    std::atomic<uint32_t> bundles_sent;
    std::atomic<uint32_t> tx_dropped;
    std::atomic<uint32_t> no_client_discards;
    std::atomic<uint32_t> events_dropped;
    std::atomic<uint32_t> clients;
    std::atomic<uint32_t> cmd_ok;
    std::atomic<uint32_t> cmd_err;
    std::atomic<uint32_t> rx_crc_errors;
    std::atomic<uint32_t> rx_oversize;
    std::atomic<uint32_t> wifi_disconnects;
    std::atomic<uint64_t> bytes_sent;
};
extern counters_t g_counters;

/**
 * The steps of one frame that the board times, in microseconds. Their names on the laptop are
 * the same with "t_" in front (t_wait_frame, t_decode, ...); docs/experiments.md, section 1,
 * explains each one.
 */
enum class stage_t : uint8_t {
    /** Blocked in esp_camera_fb_get(). */
    wait_frame,
    /** JPEG to RGB888. */
    decode,
    /** Detector preprocess: resize and quantize to 224x224. */
    pre,
    /** The neural network only. */
    infer,
    /** Detector postprocess: decode boxes, NMS. */
    post,
    grid,
    /** JPEG copy into the transmit pool. */
    copy,
    /** Hand-off to the network queue, including any wait. */
    enqueue,
    /** From esp_camera_fb_get() returning to the bundle being queued: device processing latency. */
    proc,
    /** Net task: writing one bundle to the socket. */
    send,
};
constexpr size_t STAGE_COUNT = 10;

/** Every stage in order, for loops: `for (stage_t stage : ALL_STAGES)`. */
constexpr std::array<stage_t, STAGE_COUNT> ALL_STAGES = {
    stage_t::wait_frame, stage_t::decode, stage_t::pre,     stage_t::infer, stage_t::post,
    stage_t::grid,       stage_t::copy,   stage_t::enqueue, stage_t::proc,  stage_t::send,
};

/** Position of a stage in arrays of STAGE_COUNT entries. */
constexpr size_t stage_index(stage_t stage)
{
    return static_cast<size_t>(stage);
}

/** "t_wait_frame", "t_decode", ...: the stage's name on the laptop. */
const char *stage_name(stage_t stage);

/** Value stored for a stage that was not measured for this frame. */
constexpr int32_t STAGE_NOT_MEASURED = -1;

/** The time of each stage of one frame. Use it like an array indexed by stage: times[stage_t::infer]. */
struct stage_times_t {
    std::array<int32_t, STAGE_COUNT> microseconds;

    int32_t &operator[](stage_t stage)
    {
        return microseconds[stage_index(stage)];
    }

    int32_t operator[](stage_t stage) const
    {
        return microseconds[stage_index(stage)];
    }

    /** Marks every stage as not measured. */
    void clear()
    {
        microseconds.fill(STAGE_NOT_MEASURED);
    }
};

void metrics_init();
void metrics_add(stage_t stage, int64_t duration_us);
/** Snapshots and resets the current window. The caller owns the returned JSON. */
cJSON *metrics_window();
