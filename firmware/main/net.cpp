// Net task (core 0): TCP server on TCP_PORT, one client at a time. Sends HELLO on connect, then
// bundles (JPEG_FRAME + DETECTIONS sharing frame_id), PRESENCE_GRID, METRICS, EVENT; reads COMMANDs.
#include <cstring>
#include "app.h"
#include "esp_app_desc.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "lwip/sockets.h"
#include "protocol.h"

static const char *TAG = "net";

bool send_json(conn_t *c, uint8_t type, cJSON *obj)
{
    char *s = cJSON_PrintUnformatted(obj);
    cJSON_Delete(obj);
    if (!s) return true; // out of memory: skip this record, keep the connection
    bool ok = c->sock < 0 || proto_send(c->sock, &c->seq, type, 0, s, strlen(s));
    cJSON_free(s);
    return ok;
}

cJSON *hello_json()
{
    const esp_app_desc_t *app = esp_app_get_description();
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    uint32_t flash = 0;
    esp_flash_get_size(NULL, &flash);
    uint8_t mac[6];
    device_mac(mac);
    char macs[18], id[16];
    snprintf(macs, sizeof macs, "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    snprintf(id, sizeof id, "gmt-%02x%02x%02x", mac[3], mac[4], mac[5]);

    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "proto_version", PROTO_VERSION);
    cJSON_AddStringToObject(o, "device_id", id);
    cJSON_AddStringToObject(o, "mac", macs);
    cJSON_AddStringToObject(o, "fw_version", app->version); // git describe at build time
    char built[40];
    snprintf(built, sizeof built, "%s %s", app->date, app->time);
    cJSON_AddStringToObject(o, "fw_build", built);
    cJSON_AddStringToObject(o, "idf", app->idf_ver);
    cJSON_AddStringToObject(o, "esp_dl", ESP_DL_VER);
    cJSON_AddStringToObject(o, "esp32_camera", ESP32_CAMERA_VER);
    cJSON_AddStringToObject(o, "pedestrian_detect", PEDESTRIAN_DETECT_VER);
    cJSON_AddStringToObject(o, "model", "pico_s8_v1_s3");
    cJSON_AddStringToObject(o, "chip", "esp32s3");
    char rev[8];
    snprintf(rev, sizeof rev, "v%d.%d", chip.revision / 100, chip.revision % 100);
    cJSON_AddStringToObject(o, "chip_rev", rev);
    cJSON_AddNumberToObject(o, "flash_bytes", flash);
    cJSON_AddNumberToObject(o, "psram_bytes", esp_psram_get_size());
    char pid[8];
    snprintf(pid, sizeof pid, "0x%04x", camera_sensor_pid());
    cJSON_AddStringToObject(o, "sensor_pid", pid);
    cJSON_AddNumberToObject(o, "grid_cols", GRID_COLS);
    cJSON_AddNumberToObject(o, "grid_rows", GRID_ROWS);
    cJSON_AddStringToObject(o, "box_coords", "frame_px"); // pixels of the paired frame (or the still)
    cJSON_AddStringToObject(o, "reset_reason", reset_reason_str());
    cJSON_AddStringToObject(o, "wifi", wifi_state());
    cJSON_AddStringToObject(o, "ip", wifi_ip());
    settings_to_json(cJSON_AddObjectToObject(o, "config"));
    return o;
}

static bool send_bundle(conn_t *c, const bundle_t *b, bool verbose)
{
    if (b->jpeg_len) {
        struct __attribute__((packed)) {
            uint16_t w, h;
            uint8_t quality, pad[3];
            int64_t t_capture_us;
        } jh = {b->w, b->h, settings_get().jpeg_quality, {0, 0, 0}, b->t_capture_us};
        if (!proto_send(c->sock, &c->seq, REC_JPEG_FRAME, b->frame_id, &jh, sizeof jh, b->jpeg, b->jpeg_len))
            return false;
    }
    struct __attribute__((packed)) wire_box {
        int16_t x1, y1, x2, y2;
        float score;
        uint8_t cls, pad[3]; // cls 0 = person
    };
    uint8_t buf[8 + MAX_BOXES * sizeof(wire_box)];
    buf[0] = 0; // coordinate space 0 = frame_px
    buf[1] = b->nbox;
    memcpy(buf + 2, &b->w, 2);
    memcpy(buf + 4, &b->h, 2);
    buf[6] = buf[7] = 0;
    for (int i = 0; i < b->nbox; i++) {
        wire_box w = {b->box[i].x1, b->box[i].y1, b->box[i].x2, b->box[i].y2, b->box[i].score, 0, {0, 0, 0}};
        memcpy(buf + 8 + i * sizeof w, &w, sizeof w);
    }
    if (!proto_send(c->sock, &c->seq, REC_DETECTIONS, b->frame_id, buf, 8 + b->nbox * sizeof(wire_box))) return false;
    if (!verbose) return true;
    cJSON *t = cJSON_CreateObject();
    cJSON_AddNumberToObject(t, "frame_id", b->frame_id);
    for (int i = 0; i < ST_COUNT; i++)
        if (b->t_us[i] >= 0) cJSON_AddNumberToObject(t, STAGE_NAMES[i], b->t_us[i]);
    char *s = cJSON_PrintUnformatted(t);
    cJSON_Delete(t);
    bool ok = !s || proto_send(c->sock, &c->seq, REC_FRAME_TIMING, b->frame_id, s, strlen(s));
    cJSON_free(s);
    return ok;
}

static bool send_grid(conn_t *c)
{
    static uint8_t buf[20 + GRID_COLS * GRID_ROWS * 4];
    size_t n = grid_payload(buf, sizeof buf);
    return proto_send(c->sock, &c->seq, REC_PRESENCE_GRID, 0, buf, n);
}

static void on_record(uint8_t type, const uint8_t *p, uint32_t len, void *ctx)
{
    if (type == REC_COMMAND) command_dispatch(p, len, (conn_t *)ctx);
}

// Metrics window, thermal check, drop-burst event, serial summary. c may have sock < 0 (no client).
static bool tick(conn_t *c, int64_t &next)
{
    int64_t now = esp_timer_get_time();
    if (now < next) return true;
    app_cfg_t cfg = settings_get();
    next = now + (int64_t)cfg.metrics_interval_ms * 1000;
    thermal_check(cfg.thermal_warn_c);
    cJSON *m = metrics_window();
    int dropped = cJSON_GetObjectItem(m, "dropped_in_window")->valueint;
    if (dropped >= QUEUE_DROP_BURST) event_post(EV_WARN, "queue_drop_burst", "%d bundles dropped in window", dropped);
    static int64_t next_serial = 0;
    if (now >= next_serial) {
        next_serial = now + SERIAL_SUMMARY_EVERY_S * 1000000LL;
        char *s = cJSON_PrintUnformatted(m);
        if (s) ESP_LOGI(TAG, "METRICS %s", s);
        cJSON_free(s);
    }
    return send_json(c, REC_METRICS, m);
}

void net_task(void *)
{
    int srv = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in addr = {};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(TCP_PORT);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(srv, (sockaddr *)&addr, sizeof addr) != 0 || listen(srv, 1) != 0) {
        event_post(EV_ERROR, "net_listen_failed", "errno=%d", errno);
        vTaskDelete(nullptr);
    }
    static proto_rx_t rx;
    int64_t next_tick = 0;
    for (;;) {
        conn_t c = {-1, 0, false, false, false};
        // No client: keep vision running by recycling bundles; events stay queued for the next client.
        while (c.sock < 0) {
            fd_set rf;
            FD_ZERO(&rf);
            FD_SET(srv, &rf);
            timeval tv = {0, 50000};
            if (select(srv + 1, &rf, nullptr, nullptr, &tv) > 0) c.sock = accept(srv, nullptr, nullptr);
            bundle_t *b;
            while (xQueueReceive(g_ready_q, &b, 0) == pdTRUE) {
                xQueueSend(g_free_q, &b, 0);
                g_cnt.no_client_discards++;
            }
            conn_t none = {-1, 0, false, false, false};
            tick(&none, next_tick);
        }
        timeval to = {SEND_TIMEOUT_S, 0};
        setsockopt(c.sock, SOL_SOCKET, SO_SNDTIMEO, &to, sizeof to);
        setsockopt(c.sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        rx.len = 0;
        g_cnt.clients++;
        event_post(EV_INFO, "client_connected", "count=%u", (unsigned)g_cnt.clients);

        uint32_t frames_since_grid = 0;
        bool ok = send_json(&c, REC_HELLO, hello_json());
        while (ok) {
            app_cfg_t cfg = settings_get();
            bundle_t *b;
            if (xQueueReceive(g_ready_q, &b, pdMS_TO_TICKS(20)) == pdTRUE) {
                int64_t t0 = esp_timer_get_time();
                ok = send_bundle(&c, b, cfg.metrics_verbose);
                if (ok) {
                    metrics_add(ST_SEND, esp_timer_get_time() - t0);
                    g_cnt.bundles_sent++;
                    frames_since_grid++;
                }
                xQueueSend(g_free_q, &b, 0);
            }
            event_t e;
            while (ok && event_pop(&e)) {
                cJSON *j = cJSON_CreateObject();
                cJSON_AddStringToObject(j, "severity", e.sev == EV_ERROR ? "error" : e.sev == EV_WARN ? "warn" : "info");
                cJSON_AddStringToObject(j, "code", e.code);
                cJSON_AddStringToObject(j, "text", e.text);
                cJSON_AddNumberToObject(j, "t_event_us", (double)e.t_us);
                ok = send_json(&c, REC_EVENT, j);
            }
            uint8_t in[256];
            int r = -1;
            while (ok && (r = recv(c.sock, in, sizeof in, MSG_DONTWAIT)) != 0) {
                if (r < 0) {
                    if (errno != EAGAIN && errno != EWOULDBLOCK) ok = false;
                    break;
                }
                proto_rx_feed(&rx, in, r, on_record, &c);
            }
            if (r == 0) ok = false; // client closed
            bool streaming = cfg.mode == MODE_METADATA_STREAM || cfg.mode == MODE_FULL_STREAM;
            if (ok && (c.want_grid || (streaming && frames_since_grid >= cfg.grid_every_n))) {
                ok = send_grid(&c);
                c.want_grid = false;
                frames_since_grid = 0;
            }
            if (ok && c.want_hello) {
                ok = send_json(&c, REC_HELLO, hello_json());
                c.want_hello = false;
            }
            if (ok) ok = tick(&c, next_tick);
            if (c.reboot) {
                close(c.sock);
                vTaskDelay(pdMS_TO_TICKS(200));
                esp_restart();
            }
        }
        event_post(EV_WARN, "client_lost", "errno=%d", errno);
        close(c.sock);
    }
}
