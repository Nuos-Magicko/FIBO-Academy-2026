// Shared types, counters and module entry points.
#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#include "cJSON.h"
#include "config.h"
#include "esp_camera.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

// ---------------------------------------------------------------- modes and settings
enum app_mode_t : uint8_t {
    MODE_CAMERA_ONLY,
    MODE_INFERENCE_ONLY,
    MODE_DETECT_NO_STREAM,
    MODE_METADATA_STREAM,
    MODE_FULL_STREAM,
    MODE_COUNT
};
const char *mode_name(app_mode_t m);
const char *frame_size_name(framesize_t f);

struct app_cfg_t {
    uint32_t version; // SETTINGS_VERSION; NVS blob with another value is ignored
    app_mode_t mode;
    framesize_t frame_size;
    uint8_t jpeg_quality;
    uint32_t xclk_hz;
    uint16_t warmup_frames;
    uint16_t infer_every_n;
    uint16_t infer_only_runs;
    uint32_t metrics_interval_ms;
    uint16_t grid_every_n;
    float score_thr;
    float thermal_warn_c;
    bool metrics_verbose;
    bool wifi_ap; // true: SoftAP, false: station
    char sta_ssid[33];
    char sta_pass[65];
};

void settings_init();
app_cfg_t settings_get();
uint32_t settings_generation(); // bumps on every successful set
// Apply {"key": value, ...}. Validates everything first; nothing changes on error.
bool settings_set(const cJSON *values, char *err, size_t errlen);
bool settings_save();
void settings_to_json(cJSON *obj); // never includes sta_pass

// ---------------------------------------------------------------- counters (since boot)
struct counters_t {
    std::atomic<uint32_t> cap_ok, cap_timeout, no_eoi, no_soi, fb_ovf, decode_fail, oversize;
    std::atomic<uint32_t> frames_processed, cadence_skipped, still_runs;
    std::atomic<uint32_t> bundles_sent, tx_dropped, no_client_discards, events_dropped;
    std::atomic<uint32_t> clients, cmd_ok, cmd_err, rx_crc_errors, rx_oversize, wifi_disconnects;
    std::atomic<uint64_t> bytes_sent;
};
extern counters_t g_cnt;

// ---------------------------------------------------------------- timing stages (handoff F5)
enum stage_t {
    ST_WAIT_FRAME, // blocked in esp_camera_fb_get
    ST_DECODE,     // JPEG -> RGB888
    ST_PRE,        // detector preprocess (resize/quantize to 224x224)
    ST_INFER,      // model only
    ST_POST,       // detector postprocess (decode boxes, NMS)
    ST_GRID,       // presence grid update
    ST_COPY,       // JPEG copy into the tx pool
    ST_ENQUEUE,    // enqueue incl. any wait
    ST_PROC,       // fb_get return -> bundle enqueued (device processing latency)
    ST_SEND,       // net task: socket write of one bundle
    ST_COUNT
};
extern const char *const STAGE_NAMES[ST_COUNT];

void metrics_init();
void metrics_add(stage_t s, int64_t us);
cJSON *metrics_window(); // snapshot + reset the current window; caller owns the JSON

// ---------------------------------------------------------------- frame bundles
struct box_t {
    int16_t x1, y1, x2, y2;
    float score;
};
struct bundle_t {
    uint32_t frame_id;
    int64_t t_capture_us; // esp_timer time when esp_camera_fb_get returned
    uint16_t w, h;        // coordinate space of the boxes (= frame size, or the still image)
    uint32_t jpeg_len;    // 0 = no JPEG in this bundle
    uint8_t nbox;
    box_t box[MAX_BOXES];
    int32_t t_us[ST_COUNT]; // per-frame stage times, -1 = not measured (sent when metrics_verbose)
    uint8_t *jpeg;          // PSRAM, JPEG_MAX bytes
};
extern QueueHandle_t g_free_q, g_ready_q;
void pool_init();

// ---------------------------------------------------------------- events
enum ev_sev_t : uint8_t { EV_INFO, EV_WARN, EV_ERROR };
struct event_t {
    int64_t t_us;
    ev_sev_t sev;
    char code[24];
    char text[96];
};
void events_init();
void event_post(ev_sev_t sev, const char *code, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
bool event_pop(event_t *out);

// ---------------------------------------------------------------- grid
void grid_reset(uint16_t frame_w, uint16_t frame_h);
void grid_clear(); // reset counts, keep the frame size
void grid_add(const box_t *boxes, int n);
size_t grid_payload(uint8_t *buf, size_t cap); // PRESENCE_GRID payload
uint32_t grid_frames();

// ---------------------------------------------------------------- camera
bool camera_start(const app_cfg_t &cfg); // init + PID check + warm-up
void camera_stop();
void camera_capture_test(int n);
void camera_install_log_counter(); // counts cam_hal NO-EOI/NO-SOI/FB-OVF log lines
uint16_t camera_sensor_pid();

// ---------------------------------------------------------------- thermal
void thermal_init();
float thermal_read_c();
void thermal_check(float warn_c); // posts EVENT on crossing, with hysteresis

// ---------------------------------------------------------------- wifi / net / vision
void wifi_start(const app_cfg_t &cfg);
const char *wifi_state(); // "sta_connecting" | "sta_connected" | "ap"
const char *wifi_ip();
int wifi_rssi();
uint32_t wifi_reconnects();

void net_task(void *);
void vision_task(void *);
cJSON *hello_json();

// One TCP client connection, owned by the net task.
struct conn_t {
    int sock;
    uint32_t seq; // per-connection record counter
    bool want_grid, want_hello, reboot;
};
// Handles one COMMAND payload (JSON). Replies with PONG directly, otherwise via EVENT cmd_ok/cmd_error.
void command_dispatch(const uint8_t *payload, uint32_t len, conn_t *c);
bool send_json(conn_t *c, uint8_t type, cJSON *obj); // frees obj

// ---------------------------------------------------------------- misc
const char *reset_reason_str();
void device_mac(uint8_t mac[6]);
void cjson_use_psram();
