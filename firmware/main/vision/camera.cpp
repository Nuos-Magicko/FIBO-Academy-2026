/**
 * @file camera.cpp
 * OV3660 on the XIAO ESP32S3 Sense: start/stop, sensor check, warm-up and the boot capture test.
 */
#include <cstdarg>
#include <cstring>
#include "vision/camera.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "camera";
static uint16_t s_sensor_pid;
static bool s_running;
static vprintf_like_t s_next_log_writer;

uint16_t camera_sensor_pid() { return s_sensor_pid; }

/**
 * Log writer that counts esp32-camera's NO-EOI / NO-SOI / FB-OVF messages before printing them.
 * It matches the driver's format strings, so a rewording in a future esp32-camera release would
 * silently stop the counting.
 */
static int count_camera_errors_then_log(const char *fmt, va_list args)
{
    if (strstr(fmt, "NO-EOI")) g_counters.no_eoi++;
    else if (strstr(fmt, "NO-SOI")) g_counters.no_soi++;
    else if (strstr(fmt, "FB-OVF")) g_counters.fb_ovf++;
    return s_next_log_writer(fmt, args);
}

void camera_install_log_counter() { s_next_log_writer = esp_log_set_vprintf(count_camera_errors_then_log); }

/** XIAO ESP32S3 Sense pin map (Seeed wiki). PWDN and RESET are not connected. */
static camera_config_t xiao_sense_camera_config(const app_cfg_t &cfg)
{
    camera_config_t c = {};
    c.pin_pwdn = -1;
    c.pin_reset = -1;
    c.pin_xclk = 10;
    c.pin_sccb_sda = 40;
    c.pin_sccb_scl = 39;
    c.pin_d7 = 48;
    c.pin_d6 = 11;
    c.pin_d5 = 12;
    c.pin_d4 = 14;
    c.pin_d3 = 16;
    c.pin_d2 = 18;
    c.pin_d1 = 17;
    c.pin_d0 = 15;
    c.pin_vsync = 38;
    c.pin_href = 47;
    c.pin_pclk = 13;
    c.xclk_freq_hz = cfg.xclk_hz;
    c.ledc_timer = LEDC_TIMER_0;
    c.ledc_channel = LEDC_CHANNEL_0;
    c.pixel_format = PIXFORMAT_JPEG;
    c.frame_size = cfg.frame_size;
    c.jpeg_quality = cfg.jpeg_quality;
    c.fb_count = CAMERA_FRAME_BUFFER_COUNT;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_LATEST;
    return c;
}

static void discard_frames(int count)
{
    for (int i = 0; i < count; i++) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame) esp_camera_fb_return(frame);
    }
}

bool camera_start(const app_cfg_t &cfg)
{
    camera_config_t config = xiao_sense_camera_config(cfg);
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        event_post(EV_ERROR, "camera_init_failed", "%s (frame=%s xclk=%u)", esp_err_to_name(err),
                   frame_size_name(cfg.frame_size), (unsigned)cfg.xclk_hz);
        return false;
    }
    s_running = true;
    s_sensor_pid = esp_camera_sensor_get()->id.PID;
    ESP_LOGI(TAG, "sensor PID=0x%04x frame=%s q=%u xclk=%u", s_sensor_pid, frame_size_name(cfg.frame_size),
             cfg.jpeg_quality, (unsigned)cfg.xclk_hz);
    if (s_sensor_pid != EXPECTED_SENSOR_PID)
        event_post(EV_ERROR, "sensor_mismatch", "expected 0x%04x got 0x%04x", EXPECTED_SENSOR_PID, s_sensor_pid);
    discard_frames(cfg.warmup_frames);
    return true;
}

void camera_stop()
{
    if (s_running) esp_camera_deinit();
    s_running = false;
}

void camera_capture_test(int n)
{
    uint32_t ok = 0, failed = 0, min_bytes = UINT32_MAX, max_bytes = 0;
    uint32_t no_eoi_before = g_counters.no_eoi;
    uint64_t total_bytes = 0;
    int64_t started_us = esp_timer_get_time();
    for (int i = 0; i < n; i++) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (!frame) {
            failed++;
            continue;
        }
        ok++;
        total_bytes += frame->len;
        min_bytes = frame->len < min_bytes ? frame->len : min_bytes;
        max_bytes = frame->len > max_bytes ? frame->len : max_bytes;
        esp_camera_fb_return(frame);
    }
    double fps = n * 1e6 / (esp_timer_get_time() - started_us);
    event_post(failed ? EV_WARN : EV_INFO, "capture_test", "n=%d ok=%u fail=%u no_eoi=%u bytes=%u/%u/%u fps=%.1f", n,
               (unsigned)ok, (unsigned)failed, (unsigned)(g_counters.no_eoi - no_eoi_before),
               (unsigned)(ok ? min_bytes : 0), (unsigned)(ok ? total_bytes / ok : 0), (unsigned)max_bytes, fps);
}
