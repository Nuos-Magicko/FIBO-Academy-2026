// Gate 1 spike: board facts, camera sensor check, 1000-capture test, detector on the
// embedded still, then live capture + detection streamed over Wi-Fi TCP (soak test).
// ponytail: single file on purpose; Gate 2 splits it into camera/vision/net/protocol modules.
#include <atomic>
#include <cmath>
#include <cstdarg>
#include <cstring>
#include <list>
#include "dl_image_jpeg.hpp"
#include "driver/temperature_sensor.h"
#include "esp_camera.h"
#include "esp_chip_info.h"
#include "esp_event.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_psram.h"
#include "esp_rom_crc.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "pedestrian_detect.hpp"

// git-ignored; defines WIFI_STA_SSID and WIFI_STA_PASS. Creating or deleting it is not seen by an
// incremental build: run `idf.py fullclean` (or touch main.cpp) after adding/removing the file.
// ponytail: Gate 2 moves credentials to NVS, set via COMMAND, and this goes away.
#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#endif

static const char *TAG = "gmt";

extern const uint8_t pedestrian_jpg_start[] asm("_binary_pedestrian_jpg_start");
extern const uint8_t pedestrian_jpg_end[] asm("_binary_pedestrian_jpg_end");

// Camera defaults (handoff F1). Fallback for JPEG timeouts: XCLK_HZ 10 MHz.
#define XCLK_HZ 20000000
#define FRAME_SIZE FRAMESIZE_QVGA
#define JPEG_QUALITY 12
#define FB_COUNT 2
#define WARMUP_FRAMES 10
#define CAPTURE_TEST_N 1000
#define STILL_RUNS 5
#define EXPECTED_PID 0x3660

// Network
#define TCP_PORT 5000
#define AP_PASS "gmtcamp2026" // SoftAP fallback when no wifi_secrets.h
#define SEND_TIMEOUT_S 5
#define METRICS_PERIOD_US 1000000
#define SOAK_LOG_PERIOD_US 10000000

// Frame bundle pool: 1 filling + 1 sending + up to 2 queued. Fixed at boot, no per-frame malloc.
#define POOL_N 4
#define READY_DEPTH 2
#define JPEG_MAX (32 * 1024)
#define MAX_BOXES 16

// Wire record types (handoff §6)
enum : uint8_t { REC_JPEG_FRAME = 2, REC_DETECTIONS = 3, REC_METRICS = 5 };

struct box_t {
    int16_t x1, y1, x2, y2;
    float score;
};
struct bundle_t {
    uint32_t frame_id;
    int64_t t_capture_us;
    uint16_t w, h;
    uint32_t jpeg_len;
    uint8_t nbox;
    box_t box[MAX_BOXES];
    uint8_t *jpeg; // PSRAM, JPEG_MAX bytes
};
static bundle_t s_pool[POOL_N];
static QueueHandle_t s_free_q, s_ready_q;

// Counters shared between tasks
static std::atomic<uint32_t> s_no_eoi{0}, s_no_soi{0}, s_fb_ovf{0};
static std::atomic<uint32_t> s_frames{0}, s_sent{0}, s_tx_dropped{0}, s_no_client_discards{0};
static std::atomic<uint32_t> s_cap_fail{0}, s_dec_fail{0}, s_oversize{0}, s_sta_disconnects{0}, s_clients{0};
static std::atomic<int64_t> s_t_detect_last{0}, s_t_proc_last{0}, s_t_send_last{0};
static std::atomic<uint32_t> s_jpeg_len_last{0};
static char s_ip[16] = "0.0.0.0";

// cam_hal reports NO-EOI etc. only via ESP_LOG, so count them from the log stream.
// ponytail: substring match on the format string; breaks if esp32-camera rewords it.
static vprintf_like_t s_orig_vprintf;
static int counting_vprintf(const char *fmt, va_list ap)
{
    if (strstr(fmt, "NO-EOI")) s_no_eoi++;
    else if (strstr(fmt, "NO-SOI")) s_no_soi++;
    else if (strstr(fmt, "FB-OVF")) s_fb_ovf++;
    return s_orig_vprintf(fmt, ap);
}

static temperature_sensor_handle_t s_tsens;
static float die_temp_c()
{
    float t = NAN;
    if (s_tsens) temperature_sensor_get_celsius(s_tsens, &t);
    return t;
}

static const char *reset_reason_str(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_EXT: return "EXT";
    case ESP_RST_SW: return "SW";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SDIO: return "SDIO";
    case ESP_RST_USB: return "USB";
    case ESP_RST_JTAG: return "JTAG";
    default: return "UNKNOWN";
    }
}

static int rssi()
{
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}

static void log_mem(const char *when)
{
    ESP_LOGI(TAG, "MEM %s: internal free=%u min=%u | psram free=%u min=%u | temp=%.1fC", when,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM), die_temp_c());
}

static void report_board()
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    uint32_t flash = 0;
    esp_flash_get_size(NULL, &flash);
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    ESP_LOGI(TAG, "BOARD chip=esp32s3 rev=v%d.%d cores=%d flash=%uMB psram=%uMB mac=%02x:%02x:%02x:%02x:%02x:%02x",
             chip.revision / 100, chip.revision % 100, chip.cores, (unsigned)(flash >> 20),
             (unsigned)(esp_psram_get_size() >> 20), mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    ESP_LOGI(TAG, "BOARD reset_reason=%s idf=%s", reset_reason_str(esp_reset_reason()), esp_get_idf_version());
}

// ---------------------------------------------------------------- camera

static bool camera_init()
{
    // XIAO ESP32S3 Sense camera pins (Seeed wiki)
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
    c.xclk_freq_hz = XCLK_HZ;
    c.ledc_timer = LEDC_TIMER_0;
    c.ledc_channel = LEDC_CHANNEL_0;
    c.pixel_format = PIXFORMAT_JPEG;
    c.frame_size = FRAME_SIZE;
    c.jpeg_quality = JPEG_QUALITY;
    c.fb_count = FB_COUNT;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_LATEST;

    esp_err_t err = esp_camera_init(&c);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "CAMERA init failed: %s", esp_err_to_name(err));
        return false;
    }
    sensor_t *s = esp_camera_sensor_get();
    ESP_LOGI(TAG, "CAMERA sensor PID=0x%04x VER=0x%02x MIDH=0x%02x MIDL=0x%02x xclk=%dHz",
             s->id.PID, s->id.VER, s->id.MIDH, s->id.MIDL, XCLK_HZ);
    if (s->id.PID != EXPECTED_PID)
        ESP_LOGE(TAG, "EVENT sensor_mismatch expected=0x%04x got=0x%04x", EXPECTED_PID, s->id.PID);
    return true;
}

static void capture_test()
{
    for (int i = 0; i < WARMUP_FRAMES; i++) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb) esp_camera_fb_return(fb);
    }
    s_no_eoi = s_no_soi = s_fb_ovf = 0;
    uint32_t ok = 0, fail = 0, min_len = UINT32_MAX, max_len = 0;
    uint64_t sum_len = 0;
    int64_t t0 = esp_timer_get_time();
    for (int i = 0; i < CAPTURE_TEST_N; i++) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (!fb) {
            fail++;
            continue;
        }
        ok++;
        sum_len += fb->len;
        if (fb->len < min_len) min_len = fb->len;
        if (fb->len > max_len) max_len = fb->len;
        if (i == 0) ESP_LOGI(TAG, "CAPTURE first frame %ux%u fmt=%d", fb->width, fb->height, fb->format);
        esp_camera_fb_return(fb);
    }
    int64_t dt = esp_timer_get_time() - t0;
    ESP_LOGI(TAG, "CAPTURE_TEST n=%d ok=%u fail=%u no_eoi=%u no_soi=%u fb_ovf=%u bytes min/mean/max=%u/%u/%u fps=%.2f",
             CAPTURE_TEST_N, (unsigned)ok, (unsigned)fail, (unsigned)s_no_eoi, (unsigned)s_no_soi,
             (unsigned)s_fb_ovf, (unsigned)(ok ? min_len : 0), (unsigned)(ok ? sum_len / ok : 0),
             (unsigned)max_len, CAPTURE_TEST_N * 1e6 / dt);
}

static void print_results(const char *tag, uint32_t n, std::list<dl::detect::result_t> &res)
{
    ESP_LOGI(TAG, "%s frame=%u boxes=%u", tag, (unsigned)n, (unsigned)res.size());
    for (auto &r : res)
        ESP_LOGI(TAG, "  box score=%.3f x1=%d y1=%d x2=%d y2=%d", r.score, r.box[0], r.box[1], r.box[2], r.box[3]);
}

static void still_test(PedestrianDetect *det)
{
    dl::image::jpeg_img_t jpeg = {.data = (void *)pedestrian_jpg_start,
                                  .data_len = (size_t)(pedestrian_jpg_end - pedestrian_jpg_start)};
    for (int i = 0; i < STILL_RUNS; i++) {
        int64_t t0 = esp_timer_get_time();
        auto img = dl::image::sw_decode_jpeg(jpeg, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
        int64_t t1 = esp_timer_get_time();
        auto &res = det->run(img);
        int64_t t2 = esp_timer_get_time();
        ESP_LOGI(TAG, "STILL run=%d img=%dx%d t_decode=%lldus t_detect=%lldus", i, img.width, img.height,
                 t1 - t0, t2 - t1);
        print_results("STILL", i, res);
        heap_caps_free(img.data);
    }
}

// ---------------------------------------------------------------- vision task (core 1)

static int16_t clamp16(int v) { return v < -32768 ? -32768 : v > 32767 ? 32767 : v; }

// Take a free bundle; if none, steal the oldest queued one (drop-oldest backpressure).
static bundle_t *take_bundle()
{
    bundle_t *b;
    if (xQueueReceive(s_free_q, &b, 0) == pdTRUE) return b;
    if (xQueueReceive(s_ready_q, &b, pdMS_TO_TICKS(100)) == pdTRUE) {
        s_tx_dropped++;
        return b;
    }
    return nullptr;
}

static void vision_task(void *arg)
{
    PedestrianDetect *det = (PedestrianDetect *)arg;
    uint32_t frame_id = 0;
    for (;;) {
        vTaskDelay(1); // let IDLE1 run (task watchdog). 1 tick = 1 ms at CONFIG_FREERTOS_HZ=1000.
        camera_fb_t *fb = esp_camera_fb_get();
        int64_t t_got = esp_timer_get_time();
        if (!fb) {
            ESP_LOGW(TAG, "EVENT capture_timeout total=%u", (unsigned)++s_cap_fail);
            continue;
        }
        if (fb->len > JPEG_MAX) {
            s_oversize++;
            esp_camera_fb_return(fb);
            continue;
        }
        bundle_t *b = take_bundle();
        if (!b) { // net task holds everything; should not happen with POOL_N > READY_DEPTH + 1
            esp_camera_fb_return(fb);
            continue;
        }
        // Copy JPEG into the pooled buffer, return the camera buffer right away, decode from the copy.
        memcpy(b->jpeg, fb->buf, fb->len);
        b->jpeg_len = fb->len;
        b->w = fb->width;
        b->h = fb->height;
        b->t_capture_us = t_got;
        esp_camera_fb_return(fb);

        dl::image::jpeg_img_t jpeg = {.data = b->jpeg, .data_len = b->jpeg_len};
        auto img = dl::image::sw_decode_jpeg(jpeg, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
        if (!img.data) {
            s_dec_fail++;
            xQueueSend(s_free_q, &b, 0);
            continue;
        }
        int64_t t_det0 = esp_timer_get_time();
        auto &res = det->run(img);
        int64_t t_det1 = esp_timer_get_time();
        heap_caps_free(img.data);

        b->frame_id = ++frame_id;
        b->nbox = 0;
        for (auto &r : res) {
            if (b->nbox == MAX_BOXES) break;
            b->box[b->nbox++] = {clamp16(r.box[0]), clamp16(r.box[1]), clamp16(r.box[2]), clamp16(r.box[3]), r.score};
        }
        // Enqueue; if the ready queue is full, recycle its oldest bundle.
        while (xQueueSend(s_ready_q, &b, 0) != pdTRUE) {
            bundle_t *old;
            if (xQueueReceive(s_ready_q, &old, 0) == pdTRUE) {
                xQueueSend(s_free_q, &old, 0);
                s_tx_dropped++;
            }
        }
        s_frames++;
        s_t_detect_last = t_det1 - t_det0;
        s_t_proc_last = esp_timer_get_time() - t_got;
        s_jpeg_len_last = b->jpeg_len;
    }
}

// ---------------------------------------------------------------- Wi-Fi

static void wifi_event(void *, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        auto *d = (wifi_event_sta_disconnected_t *)data;
        ESP_LOGW(TAG, "EVENT wifi_disconnected reason=%d count=%u", d->reason, (unsigned)++s_sta_disconnects);
        vTaskDelay(pdMS_TO_TICKS(1000));
        esp_wifi_connect(); // ponytail: fixed 1 s retry forever; Gate 2 adds finite backoff + state in METRICS
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        auto *e = (ip_event_got_ip_t *)data;
        snprintf(s_ip, sizeof s_ip, IPSTR, IP2STR(&e->ip_info.ip));
        ESP_LOGI(TAG, "NET mode=sta ip=%s port=%d", s_ip, TCP_PORT);
    }
}

static void wifi_init()
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, nullptr));

    wifi_config_t wc = {};
#ifdef WIFI_STA_SSID
    esp_netif_create_default_wifi_sta();
    strlcpy((char *)wc.sta.ssid, WIFI_STA_SSID, sizeof wc.sta.ssid);
    strlcpy((char *)wc.sta.password, WIFI_STA_PASS, sizeof wc.sta.password);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_LOGI(TAG, "NET mode=sta ssid=%s connecting", WIFI_STA_SSID);
#else
    esp_netif_create_default_wifi_ap();
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    wc.ap.ssid_len = snprintf((char *)wc.ap.ssid, sizeof wc.ap.ssid, "GMT-PF-%02X%02X", mac[4], mac[5]);
    strlcpy((char *)wc.ap.password, AP_PASS, sizeof wc.ap.password);
    wc.ap.channel = 6;
    wc.ap.max_connection = 2;
    wc.ap.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wc));
    strcpy(s_ip, "192.168.4.1");
    ESP_LOGI(TAG, "NET mode=ap ssid=%s ip=%s port=%d", (char *)wc.ap.ssid, s_ip, TCP_PORT);
#endif
    ESP_ERROR_CHECK(esp_wifi_start()); // default power save (MIN_MODEM in STA); recorded, not tuned
}

// ---------------------------------------------------------------- net task (core 0)

static uint32_t s_seq; // per-connection, net task only

static bool send_all(int s, const void *p, size_t n)
{
    const uint8_t *c = (const uint8_t *)p;
    while (n) {
        int r = send(s, c, n, 0);
        if (r <= 0) return false;
        c += r;
        n -= r;
    }
    return true;
}

// Header: magic[4] ver u8 type u8 flags u16 seq u32 frame_id u32 t_dev_us i64 len u32, then payload, crc32 (LE, zlib).
static bool send_record(int s, uint8_t type, uint32_t frame_id, const void *a, uint32_t alen,
                        const void *b2 = nullptr, uint32_t blen = 0)
{
    uint8_t h[28];
    uint16_t flags = 0;
    int64_t t = esp_timer_get_time();
    uint32_t len = alen + blen;
    memcpy(h, "GMTP", 4);
    h[4] = 1;
    h[5] = type;
    memcpy(h + 6, &flags, 2);
    memcpy(h + 8, &s_seq, 4);
    memcpy(h + 12, &frame_id, 4);
    memcpy(h + 16, &t, 8);
    memcpy(h + 24, &len, 4);
    uint32_t crc = esp_rom_crc32_le(0, h, sizeof h);
    crc = esp_rom_crc32_le(crc, (const uint8_t *)a, alen);
    if (blen) crc = esp_rom_crc32_le(crc, (const uint8_t *)b2, blen);
    s_seq++;
    return send_all(s, h, sizeof h) && send_all(s, a, alen) && (!blen || send_all(s, b2, blen)) &&
           send_all(s, &crc, 4);
}

static bool send_bundle(int s, const bundle_t *b)
{
    struct __attribute__((packed)) {
        uint16_t w, h;
        uint8_t quality, pad[3];
        int64_t t_capture_us;
    } jh = {b->w, b->h, JPEG_QUALITY, {0, 0, 0}, b->t_capture_us};
    if (!send_record(s, REC_JPEG_FRAME, b->frame_id, &jh, sizeof jh, b->jpeg, b->jpeg_len)) return false;

    struct __attribute__((packed)) wire_box {
        int16_t x1, y1, x2, y2;
        float score;
        uint8_t cls, pad[3]; // cls 0 = person
    };
    uint8_t buf[4 + MAX_BOXES * sizeof(wire_box)];
    buf[0] = 0; // coordinate space 0 = qvga_px (input-image pixels)
    buf[1] = b->nbox;
    buf[2] = buf[3] = 0;
    for (int i = 0; i < b->nbox; i++) {
        wire_box w = {b->box[i].x1, b->box[i].y1, b->box[i].x2, b->box[i].y2, b->box[i].score, 0, {0, 0, 0}};
        memcpy(buf + 4 + i * sizeof w, &w, sizeof w);
    }
    return send_record(s, REC_DETECTIONS, b->frame_id, buf, 4 + b->nbox * sizeof(wire_box));
}

// ponytail: JSON METRICS payload for the soak; Gate 2 replaces it with the binary per-stage window.
static int metrics_json(char *out, size_t n)
{
    return snprintf(out, n,
                    "{\"uptime_s\":%.1f,\"temp_c\":%.1f,\"frames\":%u,\"sent\":%u,\"tx_dropped\":%u,"
                    "\"no_client_discards\":%u,\"cap_fail\":%u,\"dec_fail\":%u,\"oversize\":%u,\"no_eoi\":%u,"
                    "\"no_soi\":%u,\"fb_ovf\":%u,\"int_free\":%u,\"int_min\":%u,\"ps_free\":%u,\"ps_min\":%u,"
                    "\"rssi\":%d,\"sta_disconnects\":%u,\"clients\":%u,\"t_detect_us\":%lld,\"t_proc_us\":%lld,"
                    "\"t_send_us\":%lld,\"jpeg_len\":%u,\"reset_reason\":\"%s\"}",
                    esp_timer_get_time() / 1e6, die_temp_c(), (unsigned)s_frames, (unsigned)s_sent,
                    (unsigned)s_tx_dropped, (unsigned)s_no_client_discards, (unsigned)s_cap_fail,
                    (unsigned)s_dec_fail, (unsigned)s_oversize, (unsigned)s_no_eoi, (unsigned)s_no_soi,
                    (unsigned)s_fb_ovf, (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                    (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
                    (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
                    (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM), rssi(), (unsigned)s_sta_disconnects,
                    (unsigned)s_clients, (long long)s_t_detect_last, (long long)s_t_proc_last,
                    (long long)s_t_send_last, (unsigned)s_jpeg_len_last, reset_reason_str(esp_reset_reason()));
}

// Serial copy of the metrics every 10 s, so the USB log alone records the soak even if Wi-Fi fails.
static void soak_log_tick()
{
    static int64_t next = 0;
    int64_t now = esp_timer_get_time();
    if (now < next) return;
    next = now + SOAK_LOG_PERIOD_US;
    char m[640];
    metrics_json(m, sizeof m);
    ESP_LOGI(TAG, "SOAK %s", m);
}

static void net_task(void *)
{
    int srv = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in addr = {};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(TCP_PORT);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(srv, (sockaddr *)&addr, sizeof addr) != 0 || listen(srv, 1) != 0) {
        ESP_LOGE(TAG, "NET bind/listen failed errno=%d", errno);
        vTaskDelete(nullptr);
    }
    for (;;) {
        // No client: recycle bundles so vision keeps running, and wait for a connection.
        int cli = -1;
        while (cli < 0) {
            fd_set rf;
            FD_ZERO(&rf);
            FD_SET(srv, &rf);
            timeval tv = {0, 100000};
            if (select(srv + 1, &rf, nullptr, nullptr, &tv) > 0) cli = accept(srv, nullptr, nullptr);
            bundle_t *b;
            while (xQueueReceive(s_ready_q, &b, 0) == pdTRUE) {
                xQueueSend(s_free_q, &b, 0);
                s_no_client_discards++;
            }
            soak_log_tick();
        }
        timeval to = {SEND_TIMEOUT_S, 0};
        setsockopt(cli, SOL_SOCKET, SO_SNDTIMEO, &to, sizeof to);
        setsockopt(cli, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        s_seq = 0;
        ESP_LOGI(TAG, "EVENT client_connected count=%u", (unsigned)++s_clients);

        int64_t next_metrics = 0;
        bool ok = true;
        while (ok) {
            bundle_t *b;
            if (xQueueReceive(s_ready_q, &b, pdMS_TO_TICKS(100)) == pdTRUE) {
                int64_t t0 = esp_timer_get_time();
                ok = send_bundle(cli, b);
                s_t_send_last = esp_timer_get_time() - t0;
                xQueueSend(s_free_q, &b, 0);
                if (ok) s_sent++;
            }
            int64_t now = esp_timer_get_time();
            if (ok && now >= next_metrics) {
                char m[640];
                int n = metrics_json(m, sizeof m);
                ok = send_record(cli, REC_METRICS, 0, m, n);
                next_metrics = now + METRICS_PERIOD_US;
            }
            soak_log_tick();
        }
        ESP_LOGW(TAG, "EVENT client_lost errno=%d", errno);
        close(cli);
    }
}

// ---------------------------------------------------------------- main

extern "C" void app_main(void)
{
    s_orig_vprintf = esp_log_set_vprintf(counting_vprintf);
    temperature_sensor_config_t tcfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 100);
    if (temperature_sensor_install(&tcfg, &s_tsens) == ESP_OK) temperature_sensor_enable(s_tsens);
    else s_tsens = nullptr;

    report_board();
    log_mem("boot");

    PedestrianDetect *det = new PedestrianDetect();
    log_mem("model_loaded");
    still_test(det);

    if (!camera_init()) {
        ESP_LOGE(TAG, "STOP: no camera. Check ribbon cable / Sense board seating.");
        return;
    }
    capture_test();
    log_mem("after_capture_test");

    s_free_q = xQueueCreate(POOL_N, sizeof(bundle_t *));
    s_ready_q = xQueueCreate(READY_DEPTH, sizeof(bundle_t *));
    for (int i = 0; i < POOL_N; i++) {
        s_pool[i].jpeg = (uint8_t *)heap_caps_malloc(JPEG_MAX, MALLOC_CAP_SPIRAM);
        bundle_t *b = &s_pool[i];
        xQueueSend(s_free_q, &b, 0);
    }

    wifi_init();
    log_mem("wifi_started");
    xTaskCreatePinnedToCore(net_task, "net", 6144, nullptr, 5, nullptr, 0);
    xTaskCreatePinnedToCore(vision_task, "vision", 16384, det, 5, nullptr, 1);
}
