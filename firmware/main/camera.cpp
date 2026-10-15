// OV3660 on the XIAO ESP32S3 Sense: init/deinit, sensor check, warm-up, boot capture test.
#include <cstdarg>
#include <cstring>
#include "app.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "camera";
static uint16_t s_pid;
static bool s_running;

uint16_t camera_sensor_pid() { return s_pid; }

// cam_hal reports NO-EOI etc. only via ESP_LOG, so count them from the log stream.
// ponytail: substring match on the format string; breaks if esp32-camera rewords it.
static vprintf_like_t s_orig_vprintf;
static int counting_vprintf(const char *fmt, va_list ap)
{
    if (strstr(fmt, "NO-EOI")) g_cnt.no_eoi++;
    else if (strstr(fmt, "NO-SOI")) g_cnt.no_soi++;
    else if (strstr(fmt, "FB-OVF")) g_cnt.fb_ovf++;
    return s_orig_vprintf(fmt, ap);
}
void camera_install_log_counter() { s_orig_vprintf = esp_log_set_vprintf(counting_vprintf); }

bool camera_start(const app_cfg_t &cfg)
{
    // XIAO ESP32S3 Sense camera pins (Seeed wiki); PWDN/RESET not connected
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
    c.fb_count = CAMERA_FB_COUNT;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_LATEST;

    esp_err_t err = esp_camera_init(&c);
    if (err != ESP_OK) {
        event_post(EV_ERROR, "camera_init_failed", "%s (frame=%s xclk=%u)", esp_err_to_name(err),
                   frame_size_name(cfg.frame_size), (unsigned)cfg.xclk_hz);
        return false;
    }
    s_running = true;
    sensor_t *s = esp_camera_sensor_get();
    s_pid = s->id.PID;
    ESP_LOGI(TAG, "sensor PID=0x%04x frame=%s q=%u xclk=%u", s_pid, frame_size_name(cfg.frame_size),
             cfg.jpeg_quality, (unsigned)cfg.xclk_hz);
    if (s_pid != EXPECTED_SENSOR_PID)
        event_post(EV_ERROR, "sensor_mismatch", "expected 0x%04x got 0x%04x", EXPECTED_SENSOR_PID, s_pid);

    for (int i = 0; i < cfg.warmup_frames; i++) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb) esp_camera_fb_return(fb);
    }
    return true;
}

void camera_stop()
{
    if (s_running) esp_camera_deinit();
    s_running = false;
}

void camera_capture_test(int n)
{
    uint32_t ok = 0, fail = 0, min_len = UINT32_MAX, max_len = 0, eoi0 = g_cnt.no_eoi;
    uint64_t sum = 0;
    int64_t t0 = esp_timer_get_time();
    for (int i = 0; i < n; i++) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (!fb) {
            fail++;
            continue;
        }
        ok++;
        sum += fb->len;
        min_len = fb->len < min_len ? fb->len : min_len;
        max_len = fb->len > max_len ? fb->len : max_len;
        esp_camera_fb_return(fb);
    }
    double fps = n * 1e6 / (esp_timer_get_time() - t0);
    event_post(fail ? EV_WARN : EV_INFO, "capture_test", "n=%d ok=%u fail=%u no_eoi=%u bytes=%u/%u/%u fps=%.1f", n,
               (unsigned)ok, (unsigned)fail, (unsigned)(g_cnt.no_eoi - eoi0), (unsigned)(ok ? min_len : 0),
               (unsigned)(ok ? sum / ok : 0), (unsigned)max_len, fps);
}
