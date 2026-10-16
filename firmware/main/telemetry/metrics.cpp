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
#include <cstring>
#include "telemetry/metrics.h"
#include "config/settings.h"
#include "network/wifi.h"
#include "telemetry/thermal.h"
#include "utils/device.h"
#include "vision/bundle.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

counters_t g_counters;

const char *const STAGE_NAMES[ST_COUNT] = {"t_wait_frame", "t_decode", "t_pre",     "t_infer", "t_post",
                                           "t_grid",       "t_copy",   "t_enqueue", "t_proc",  "t_send"};

struct stage_window_t {
    int32_t samples[METRICS_MAX_SAMPLES_PER_STAGE];
    uint32_t count, overflow;
};

/** Throughput counters at the start of the current window. */
struct window_baseline_t {
    int64_t started_us;
    uint32_t processed, sent, dropped;
    uint64_t bytes;
};

static stage_window_t *s_stage_windows;
static SemaphoreHandle_t s_lock;
static window_baseline_t s_window;

void metrics_init()
{
    s_stage_windows = (stage_window_t *)heap_caps_calloc(ST_COUNT, sizeof(stage_window_t), MALLOC_CAP_SPIRAM);
    s_lock = xSemaphoreCreateMutex();
    s_window.started_us = esp_timer_get_time();
}

void metrics_add(stage_t stage, int64_t duration_us)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    stage_window_t &window = s_stage_windows[stage];
    if (window.count < METRICS_MAX_SAMPLES_PER_STAGE) window.samples[window.count++] = (int32_t)duration_us;
    else window.overflow++;
    xSemaphoreGive(s_lock);
}

/** 1-based nearest-rank index of a percentile: ceil(percent / 100 * n). */
static uint32_t nearest_rank(uint32_t n, uint32_t percent) { return (n * percent + 99) / 100; }

static void add_stage_summary(cJSON *stages, const char *name, int32_t *samples, uint32_t count, uint32_t overflow)
{
    std::sort(samples, samples + count);
    int64_t sum = 0;
    for (uint32_t i = 0; i < count; i++) sum += samples[i];
    cJSON *summary = cJSON_AddObjectToObject(stages, name);
    cJSON_AddNumberToObject(summary, "n", count);
    cJSON_AddNumberToObject(summary, "min", samples[0]);
    cJSON_AddNumberToObject(summary, "mean", (double)sum / count);
    cJSON_AddNumberToObject(summary, "max", samples[count - 1]);
    cJSON_AddNumberToObject(summary, "p95", samples[nearest_rank(count, 95) - 1]);
    if (overflow) cJSON_AddNumberToObject(summary, "overflow", overflow);
}

/** Moves each stage's samples out under the lock, then sorts and summarizes them outside it. */
static void add_stage_summaries(cJSON *root)
{
    static int32_t samples[METRICS_MAX_SAMPLES_PER_STAGE];
    cJSON *stages = cJSON_AddObjectToObject(root, "stages");
    for (int stage = 0; stage < ST_COUNT; stage++) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        stage_window_t &window = s_stage_windows[stage];
        uint32_t count = window.count, overflow = window.overflow;
        memcpy(samples, window.samples, count * sizeof(int32_t));
        window.count = window.overflow = 0;
        xSemaphoreGive(s_lock);
        if (count) add_stage_summary(stages, STAGE_NAMES[stage], samples, count, overflow);
    }
}

static void add_throughput(cJSON *root, int64_t now_us)
{
    window_baseline_t current = {now_us, g_counters.frames_processed, g_counters.bundles_sent, g_counters.tx_dropped,
                                 g_counters.bytes_sent};
    int64_t window_us = now_us - s_window.started_us;
    double seconds = window_us / 1e6;
    cJSON_AddNumberToObject(root, "window_us", (double)window_us);
    cJSON_AddNumberToObject(root, "fps_processed", (current.processed - s_window.processed) / seconds);
    cJSON_AddNumberToObject(root, "fps_sent", (current.sent - s_window.sent) / seconds);
    cJSON_AddNumberToObject(root, "bytes_per_s", (current.bytes - s_window.bytes) / seconds);
    cJSON_AddNumberToObject(root, "dropped_in_window", current.dropped - s_window.dropped);
    s_window = current;
}

static void add_counters(cJSON *root)
{
    cJSON *counters = cJSON_AddObjectToObject(root, "counters");
#define ADD_COUNTER(name) cJSON_AddNumberToObject(counters, #name, (double)g_counters.name)
    ADD_COUNTER(cap_ok);
    ADD_COUNTER(cap_timeout);
    ADD_COUNTER(no_eoi);
    ADD_COUNTER(no_soi);
    ADD_COUNTER(fb_ovf);
    ADD_COUNTER(decode_fail);
    ADD_COUNTER(oversize);
    ADD_COUNTER(frames_processed);
    ADD_COUNTER(cadence_skipped);
    ADD_COUNTER(still_runs);
    ADD_COUNTER(bundles_sent);
    ADD_COUNTER(tx_dropped);
    ADD_COUNTER(no_client_discards);
    ADD_COUNTER(events_dropped);
    ADD_COUNTER(clients);
    ADD_COUNTER(cmd_ok);
    ADD_COUNTER(cmd_err);
    ADD_COUNTER(rx_crc_errors);
    ADD_COUNTER(rx_oversize);
    ADD_COUNTER(wifi_disconnects);
    ADD_COUNTER(bytes_sent);
#undef ADD_COUNTER
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
    cJSON_AddNumberToObject(root, "uptime_s", now_us / 1e6);
    cJSON_AddStringToObject(root, "reset_reason", reset_reason_str());
    cJSON_AddStringToObject(root, "mode", mode_name(settings_get().mode));
    return root;
}
