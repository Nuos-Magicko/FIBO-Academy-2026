/**
 * @file camera.cpp
 * OV3660 on the XIAO ESP32S3 Sense: start/stop, sensor check, warm-up and the boot capture test.
 */
#include <cstdarg>
#include <cstring>
#include "vision/camera.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "utils/units.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "camera";

/** XIAO ESP32S3 Sense camera pin map (Seeed wiki). PWDN and RESET are not connected. */
static constexpr int PIN_NOT_CONNECTED = -1;
static constexpr int PIN_XCLK = 10;
static constexpr int PIN_SCCB_SDA = 40;
static constexpr int PIN_SCCB_SCL = 39;
static constexpr int PIN_D7 = 48;
static constexpr int PIN_D6 = 11;
static constexpr int PIN_D5 = 12;
static constexpr int PIN_D4 = 14;
static constexpr int PIN_D3 = 16;
static constexpr int PIN_D2 = 18;
static constexpr int PIN_D1 = 17;
static constexpr int PIN_D0 = 15;
static constexpr int PIN_VSYNC = 38;
static constexpr int PIN_HREF = 47;
static constexpr int PIN_PCLK = 13;
static uint16_t s_sensor_pid;
static bool s_running;
static vprintf_like_t s_next_log_writer;

uint16_t camera_sensor_pid()
{
    return s_sensor_pid;
}

/**
 * Log writer that counts esp32-camera's NO-EOI / NO-SOI / FB-OVF messages before printing them.
 * It matches the driver's format strings, so a rewording in a future esp32-camera release would
 * silently stop the counting.
 */
static int count_camera_errors_then_log(const char *fmt, va_list args)
{
    if (strstr(fmt, "NO-EOI") != nullptr) {
        g_counters.no_eoi++;
    } else if (strstr(fmt, "NO-SOI") != nullptr) {
        g_counters.no_soi++;
    } else if (strstr(fmt, "FB-OVF") != nullptr) {
        g_counters.fb_ovf++;
    }
    return s_next_log_writer(fmt, args);
}

void camera_install_log_counter()
{
    s_next_log_writer = esp_log_set_vprintf(count_camera_errors_then_log);
}

/** The camera driver settings for the XIAO ESP32S3 Sense. */
static camera_config_t xiao_sense_camera_config(const app_cfg_t &settings)
{
    camera_config_t config = {};
    config.pin_pwdn = PIN_NOT_CONNECTED;
    config.pin_reset = PIN_NOT_CONNECTED;
    config.pin_xclk = PIN_XCLK;
    config.pin_sccb_sda = PIN_SCCB_SDA;
    config.pin_sccb_scl = PIN_SCCB_SCL;
    config.pin_d7 = PIN_D7;
    config.pin_d6 = PIN_D6;
    config.pin_d5 = PIN_D5;
    config.pin_d4 = PIN_D4;
    config.pin_d3 = PIN_D3;
    config.pin_d2 = PIN_D2;
    config.pin_d1 = PIN_D1;
    config.pin_d0 = PIN_D0;
    config.pin_vsync = PIN_VSYNC;
    config.pin_href = PIN_HREF;
    config.pin_pclk = PIN_PCLK;
    config.xclk_freq_hz = settings.xclk_hz;
    config.ledc_timer = LEDC_TIMER_0;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = settings.frame_size;
    config.jpeg_quality = settings.jpeg_quality;
    config.fb_count = CAMERA_FRAME_BUFFER_COUNT;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;
    return config;
}

static void discard_frames(int count)
{
    for (int index = 0; index < count; index++) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame != nullptr) {
            esp_camera_fb_return(frame);
        }
    }
}

bool camera_start(const app_cfg_t &settings)
{
    camera_config_t config = xiao_sense_camera_config(settings);
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        event_post(event_severity_t::error, "camera_init_failed", "%s (frame=%s xclk=%u)", esp_err_to_name(err),
                   frame_size_name(settings.frame_size), static_cast<unsigned>(settings.xclk_hz));
        return false;
    }
    s_running = true;
    s_sensor_pid = esp_camera_sensor_get()->id.PID;
    ESP_LOGI(TAG, "sensor PID=0x%04x frame=%s q=%u xclk=%u", s_sensor_pid, frame_size_name(settings.frame_size),
             settings.jpeg_quality, static_cast<unsigned>(settings.xclk_hz));
    if (s_sensor_pid != EXPECTED_SENSOR_PID) {
        event_post(event_severity_t::error, "sensor_mismatch", "expected 0x%04x got 0x%04x", EXPECTED_SENSOR_PID, s_sensor_pid);
    }
    discard_frames(settings.warmup_frames);
    return true;
}

void camera_stop()
{
    if (s_running) {
        esp_camera_deinit();
    }
    s_running = false;
}

void camera_capture_test(int frame_count)
{
    uint32_t ok = 0;
    uint32_t failed = 0;
    uint32_t min_bytes = UINT32_MAX;
    uint32_t max_bytes = 0;
    uint64_t total_bytes = 0;
    uint32_t no_eoi_before = g_counters.no_eoi;
    int64_t started_us = esp_timer_get_time();
    for (int index = 0; index < frame_count; index++) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame == nullptr) {
            failed++;
            continue;
        }
        ok++;
        total_bytes += frame->len;
        if (frame->len < min_bytes) {
            min_bytes = frame->len;
        }
        if (frame->len > max_bytes) {
            max_bytes = frame->len;
        }
        esp_camera_fb_return(frame);
    }
    double fps = static_cast<double>(frame_count) * MICROSECONDS_PER_SECOND / (esp_timer_get_time() - started_us);

    event_severity_t severity = event_severity_t::info;
    if (failed > 0) {
        severity = event_severity_t::warn;
    }
    uint32_t smallest = 0;
    uint32_t average = 0;
    if (ok > 0) {
        smallest = min_bytes;
        average = total_bytes / ok;
    }
    event_post(severity, "capture_test", "n=%d ok=%u fail=%u no_eoi=%u bytes=%u/%u/%u fps=%.1f", frame_count,
               static_cast<unsigned>(ok), static_cast<unsigned>(failed), static_cast<unsigned>(g_counters.no_eoi - no_eoi_before), static_cast<unsigned>(smallest),
               static_cast<unsigned>(average), static_cast<unsigned>(max_bytes), fps);
}
