// Per-window stage statistics (min/mean/max/p95 + n) and the METRICS JSON payload.
#include <algorithm>
#include <cstring>
#include "app.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/semphr.h"

const char *const STAGE_NAMES[ST_COUNT] = {"t_wait_frame", "t_decode", "t_pre",     "t_infer", "t_post",
                                           "t_grid",       "t_copy",   "t_enqueue", "t_proc",  "t_send"};

struct stage_win_t {
    int32_t s[METRICS_MAX_SAMPLES];
    uint32_t n, overflow;
};
static stage_win_t *s_win; // PSRAM, ST_COUNT entries
static SemaphoreHandle_t s_mtx;
static int64_t s_win_start;
static uint32_t s_prev_processed, s_prev_sent, s_prev_dropped;
static uint64_t s_prev_bytes;

void metrics_init()
{
    s_win = (stage_win_t *)heap_caps_calloc(ST_COUNT, sizeof(stage_win_t), MALLOC_CAP_SPIRAM);
    s_mtx = xSemaphoreCreateMutex();
    s_win_start = esp_timer_get_time();
}

void metrics_add(stage_t st, int64_t us)
{
    xSemaphoreTake(s_mtx, portMAX_DELAY);
    stage_win_t &w = s_win[st];
    if (w.n < METRICS_MAX_SAMPLES) w.s[w.n++] = (int32_t)us;
    else w.overflow++;
    xSemaphoreGive(s_mtx);
}

cJSON *metrics_window()
{
    static int32_t tmp[METRICS_MAX_SAMPLES]; // only called from the net task
    cJSON *root = cJSON_CreateObject();
    int64_t now = esp_timer_get_time();
    int64_t win_us = now - s_win_start;
    cJSON_AddNumberToObject(root, "window_us", (double)win_us);

    cJSON *stages = cJSON_AddObjectToObject(root, "stages");
    xSemaphoreTake(s_mtx, portMAX_DELAY);
    for (int i = 0; i < ST_COUNT; i++) {
        stage_win_t &w = s_win[i];
        uint32_t n = w.n, ovf = w.overflow;
        memcpy(tmp, w.s, n * sizeof(int32_t));
        w.n = w.overflow = 0;
        if (!n) continue;
        xSemaphoreGive(s_mtx); // sort outside the lock
        std::sort(tmp, tmp + n);
        int64_t sum = 0;
        for (uint32_t j = 0; j < n; j++) sum += tmp[j];
        uint32_t p95 = (n * 95 + 99) / 100; // nearest-rank: ceil(0.95 n)
        cJSON *s = cJSON_AddObjectToObject(stages, STAGE_NAMES[i]);
        cJSON_AddNumberToObject(s, "n", n);
        cJSON_AddNumberToObject(s, "min", tmp[0]);
        cJSON_AddNumberToObject(s, "mean", (double)sum / n);
        cJSON_AddNumberToObject(s, "max", tmp[n - 1]);
        cJSON_AddNumberToObject(s, "p95", tmp[p95 - 1]);
        if (ovf) cJSON_AddNumberToObject(s, "overflow", ovf);
        xSemaphoreTake(s_mtx, portMAX_DELAY);
    }
    s_win_start = now;
    xSemaphoreGive(s_mtx);

    // Throughput over this window (distinct from per-frame latency)
    uint32_t processed = g_cnt.frames_processed, sent = g_cnt.bundles_sent, dropped = g_cnt.tx_dropped;
    uint64_t bytes = g_cnt.bytes_sent;
    double secs = win_us / 1e6;
    cJSON_AddNumberToObject(root, "fps_processed", (processed - s_prev_processed) / secs);
    cJSON_AddNumberToObject(root, "fps_sent", (sent - s_prev_sent) / secs);
    cJSON_AddNumberToObject(root, "bytes_per_s", (bytes - s_prev_bytes) / secs);
    cJSON_AddNumberToObject(root, "dropped_in_window", dropped - s_prev_dropped);
    s_prev_processed = processed;
    s_prev_sent = sent;
    s_prev_dropped = dropped;
    s_prev_bytes = bytes;

    cJSON *c = cJSON_AddObjectToObject(root, "counters");
#define C(name) cJSON_AddNumberToObject(c, #name, (double)g_cnt.name)
    C(cap_ok); C(cap_timeout); C(no_eoi); C(no_soi); C(fb_ovf); C(decode_fail); C(oversize);
    C(frames_processed); C(cadence_skipped); C(still_runs); C(bundles_sent); C(tx_dropped);
    C(no_client_discards); C(events_dropped); C(clients); C(cmd_ok); C(cmd_err); C(rx_crc_errors);
    C(rx_oversize); C(wifi_disconnects); C(bytes_sent);
#undef C
    cJSON_AddNumberToObject(c, "ready_queue_depth", uxQueueMessagesWaiting(g_ready_q));

    cJSON *m = cJSON_AddObjectToObject(root, "mem");
    cJSON_AddNumberToObject(m, "int_free", heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(m, "int_min", heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(m, "ps_free", heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    cJSON_AddNumberToObject(m, "ps_min", heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM));

    cJSON_AddNumberToObject(root, "temp_c", thermal_read_c());
    cJSON_AddNumberToObject(root, "rssi", wifi_rssi());
    cJSON_AddStringToObject(root, "wifi", wifi_state());
    cJSON_AddNumberToObject(root, "wifi_reconnects", wifi_reconnects());
    cJSON_AddNumberToObject(root, "uptime_s", now / 1e6);
    cJSON_AddStringToObject(root, "reset_reason", reset_reason_str());
    cJSON_AddStringToObject(root, "mode", mode_name(settings_get().mode));
    return root;
}
