/**
 * @file metrics.cpp
 * Collects the timing of every frame and turns it into the once-per-second summary (METRICS).
 *
 * During each window (1 second by default), every timed step's durations are stored. At the end
 * of the window they are sorted and summarized as n (how many), min, mean, max and p95 (95 % of
 * the frames were this fast or faster), and sent with counters, free memory, chip temperature and
 * Wi-Fi signal strength. Then the window starts again empty.
 */
#include <algorithm>
#include <array>
#include <cstring>
#include "telemetry/metrics.h"
#include "config/settings.h"
#include "network/wifi.h"
#include "telemetry/thermal.h"
#include "utils/device.h"
#include "utils/units.h"
#include "vision/bundle.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

counters_t g_counters;

static constexpr std::array<const char *, STAGE_COUNT> STAGE_NAMES = {
    "t_wait_frame", "t_decode", "t_pre", "t_infer", "t_post", "t_grid", "t_copy", "t_enqueue", "t_proc", "t_send",
};
static constexpr uint32_t PERCENT = 100;
static constexpr uint32_t P95_PERCENT = 95;

const char *stage_name(stage_t stage)
{
    return STAGE_NAMES[stage_index(stage)];
}

struct stage_window_t {
    std::array<int32_t, METRICS_MAX_SAMPLES_PER_STAGE> samples;
    uint32_t count;
    uint32_t overflow;
};

/** Throughput counters at the start of the current window. */
struct window_baseline_t {
    int64_t started_us;
    uint32_t processed;
    uint32_t sent;
    uint32_t dropped;
    uint64_t bytes;
};

static stage_window_t *s_stage_windows;
static SemaphoreHandle_t s_lock;
static window_baseline_t s_window;

void metrics_init()
{
    void *memory = heap_caps_calloc(STAGE_COUNT, sizeof(stage_window_t), MALLOC_CAP_SPIRAM);
    s_stage_windows = static_cast<stage_window_t *>(memory);
    s_lock = xSemaphoreCreateMutex();
    s_window.started_us = esp_timer_get_time();
}

void metrics_add(stage_t stage, int64_t duration_us)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    stage_window_t &window = s_stage_windows[stage_index(stage)];
    if (window.count < window.samples.size()) {
        window.samples[window.count] = static_cast<int32_t>(duration_us);
        window.count++;
    } else {
        window.overflow++;
    }
    xSemaphoreGive(s_lock);
}

/** 1-based nearest-rank index of a percentile: ceil(percent / 100 * count). */
static uint32_t nearest_rank(uint32_t count, uint32_t percent)
{
    return (count * percent + PERCENT - 1) / PERCENT;
}

static void add_stage_summary(cJSON *stages, const char *name, int32_t *samples, uint32_t count, uint32_t overflow)
{
    std::sort(samples, samples + count);
    int64_t sum = 0;
    for (uint32_t index = 0; index < count; index++) {
        sum += samples[index];
    }
    cJSON *summary = cJSON_AddObjectToObject(stages, name);
    cJSON_AddNumberToObject(summary, "n", count);
    cJSON_AddNumberToObject(summary, "min", samples[0]);
    cJSON_AddNumberToObject(summary, "mean", static_cast<double>(sum) / count);
    cJSON_AddNumberToObject(summary, "max", samples[count - 1]);
    cJSON_AddNumberToObject(summary, "p95", samples[nearest_rank(count, P95_PERCENT) - 1]);
    if (overflow > 0) {
        cJSON_AddNumberToObject(summary, "overflow", overflow);
    }
}

/** Moves each stage's samples out under the lock, then sorts and summarizes them outside it. */
static void add_stage_summaries(cJSON *root)
{
    static std::array<int32_t, METRICS_MAX_SAMPLES_PER_STAGE> samples;
    cJSON *stages = cJSON_AddObjectToObject(root, "stages");
    for (stage_t stage : ALL_STAGES) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        stage_window_t &window = s_stage_windows[stage_index(stage)];
        uint32_t count = window.count;
        uint32_t overflow = window.overflow;
        std::copy(window.samples.begin(), window.samples.begin() + count, samples.begin());
        window.count = 0;
        window.overflow = 0;
        xSemaphoreGive(s_lock);
        if (count > 0) {
            add_stage_summary(stages, stage_name(stage), samples.data(), count, overflow);
        }
    }
}

static void add_throughput(cJSON *root, int64_t now_us)
{
    window_baseline_t current;
    current.started_us = now_us;
    current.processed = g_counters.frames_processed.load();
    current.sent = g_counters.bundles_sent.load();
    current.dropped = g_counters.tx_dropped.load();
    current.bytes = g_counters.bytes_sent.load();
    int64_t window_us = now_us - s_window.started_us;
    double seconds = static_cast<double>(window_us) / MICROSECONDS_PER_SECOND;
    cJSON_AddNumberToObject(root, "window_us", static_cast<double>(window_us));
    cJSON_AddNumberToObject(root, "fps_processed", (current.processed - s_window.processed) / seconds);
    cJSON_AddNumberToObject(root, "fps_sent", (current.sent - s_window.sent) / seconds);
    cJSON_AddNumberToObject(root, "bytes_per_s", (current.bytes - s_window.bytes) / seconds);
    cJSON_AddNumberToObject(root, "dropped_in_window", current.dropped - s_window.dropped);
    s_window = current;
}

static void add_counters(cJSON *root)
{
    cJSON *counters = cJSON_AddObjectToObject(root, "counters");
    cJSON_AddNumberToObject(counters, "cap_ok", g_counters.cap_ok.load());
    cJSON_AddNumberToObject(counters, "cap_timeout", g_counters.cap_timeout.load());
    cJSON_AddNumberToObject(counters, "no_eoi", g_counters.no_eoi.load());
    cJSON_AddNumberToObject(counters, "no_soi", g_counters.no_soi.load());
    cJSON_AddNumberToObject(counters, "fb_ovf", g_counters.fb_ovf.load());
    cJSON_AddNumberToObject(counters, "decode_fail", g_counters.decode_fail.load());
    cJSON_AddNumberToObject(counters, "oversize", g_counters.oversize.load());
    cJSON_AddNumberToObject(counters, "frames_processed", g_counters.frames_processed.load());
    cJSON_AddNumberToObject(counters, "cadence_skipped", g_counters.cadence_skipped.load());
    cJSON_AddNumberToObject(counters, "still_runs", g_counters.still_runs.load());
    cJSON_AddNumberToObject(counters, "bundles_sent", g_counters.bundles_sent.load());
    cJSON_AddNumberToObject(counters, "tx_dropped", g_counters.tx_dropped.load());
    cJSON_AddNumberToObject(counters, "no_client_discards", g_counters.no_client_discards.load());
    cJSON_AddNumberToObject(counters, "events_dropped", g_counters.events_dropped.load());
    cJSON_AddNumberToObject(counters, "clients", g_counters.clients.load());
    cJSON_AddNumberToObject(counters, "cmd_ok", g_counters.cmd_ok.load());
    cJSON_AddNumberToObject(counters, "cmd_err", g_counters.cmd_err.load());
    cJSON_AddNumberToObject(counters, "rx_crc_errors", g_counters.rx_crc_errors.load());
    cJSON_AddNumberToObject(counters, "rx_oversize", g_counters.rx_oversize.load());
    cJSON_AddNumberToObject(counters, "wifi_disconnects", g_counters.wifi_disconnects.load());
    cJSON_AddNumberToObject(counters, "bytes_sent", static_cast<double>(g_counters.bytes_sent.load()));
    cJSON_AddNumberToObject(counters, "ready_queue_depth", uxQueueMessagesWaiting(g_ready_bundles));
}

static void add_memory(cJSON *root)
{
    cJSON *mem = cJSON_AddObjectToObject(root, "mem");
    cJSON_AddNumberToObject(mem, "int_free", heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(mem, "int_min", heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(mem, "ps_free", heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    cJSON_AddNumberToObject(mem, "ps_min", heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM));
}

cJSON *metrics_window()
{
    cJSON *root = cJSON_CreateObject();
    int64_t now_us = esp_timer_get_time();
    add_throughput(root, now_us);
    add_stage_summaries(root);
    add_counters(root);
    add_memory(root);
    cJSON_AddNumberToObject(root, "temp_c", thermal_read_c());
    cJSON_AddNumberToObject(root, "rssi", wifi_rssi());
    cJSON_AddStringToObject(root, "wifi", wifi_state());
    cJSON_AddNumberToObject(root, "wifi_reconnects", wifi_reconnects());
    cJSON_AddNumberToObject(root, "uptime_s", static_cast<double>(now_us) / MICROSECONDS_PER_SECOND);
    cJSON_AddStringToObject(root, "reset_reason", reset_reason_str());
    cJSON_AddStringToObject(root, "mode", mode_name(settings_get().mode));
    return root;
}
