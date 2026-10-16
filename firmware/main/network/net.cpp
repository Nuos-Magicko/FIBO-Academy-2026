/**
 * @file net.cpp
 * The net task (core 0): the board's connection to the laptop.
 *
 * The board waits for one laptop to connect on TCP port 5000. When it does, the board first
 * sends HELLO (its details and settings), then keeps sending:
 *  - each finished frame as a JPEG_FRAME (the picture) + DETECTIONS (the boxes), with the same
 *    frame_id so the laptop can match them;
 *  - PRESENCE_GRID (the presence heatmap) now and then;
 *  - METRICS (the timing summary) once per second;
 *  - EVENT (messages such as warnings).
 * It also reads COMMAND messages from the laptop (change a setting, ping, ...).
 *
 * The time to send one frame is measured as t_send. While no laptop is connected, finished
 * frames are thrown away so the vision task never has to wait; messages are kept for the next
 * laptop that connects. The message format is described in docs/protocol.md.
 */
#include <cstring>
#include "network/net.h"
#include "config/settings.h"
#include "network/commands.h"
#include "network/protocol.h"
#include "network/wifi.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "telemetry/thermal.h"
#include "utils/device.h"
#include "vision/bundle.h"
#include "vision/camera.h"
#include "vision/grid.h"
#include "esp_app_desc.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"

static const char *TAG = "net";
static constexpr int NO_SOCKET = -1;
static constexpr uint32_t ACCEPT_POLL_US = 50000;
static constexpr uint32_t BUNDLE_WAIT_MS = 20;
static constexpr uint32_t REBOOT_DELAY_MS = 200;
static constexpr uint8_t COORDINATES_FRAME_PIXELS = 0;
static constexpr uint8_t CLASS_PERSON = 0;

struct __attribute__((packed)) jpeg_frame_header_t {
    uint16_t width, height;
    uint8_t quality, pad[3];
    int64_t t_capture_us;
};

struct __attribute__((packed)) detections_header_t {
    uint8_t coordinate_space, count;
    uint16_t width, height, pad;
};

struct __attribute__((packed)) wire_box_t {
    int16_t x1, y1, x2, y2;
    float score;
    uint8_t class_id, pad[3];
};

/** When the JSON cannot be printed (out of memory) the record is skipped but the connection kept. */
bool send_json(client_connection_t *client, uint8_t type, cJSON *obj)
{
    char *text = cJSON_PrintUnformatted(obj);
    cJSON_Delete(obj);
    if (!text) return true;
    bool sent = client->sock == NO_SOCKET || proto_send(client->sock, &client->seq, type, 0, text, strlen(text));
    cJSON_free(text);
    return sent;
}

static void add_device_identity(cJSON *hello)
{
    uint8_t mac[6];
    device_mac(mac);
    char mac_text[18], device_id[16];
    snprintf(mac_text, sizeof mac_text, "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4],
             mac[5]);
    snprintf(device_id, sizeof device_id, "gmt-%02x%02x%02x", mac[3], mac[4], mac[5]);
    cJSON_AddStringToObject(hello, "device_id", device_id);
    cJSON_AddStringToObject(hello, "mac", mac_text);
}

/** fw_version is `git describe` of the tree the firmware was built from. */
static void add_software_versions(cJSON *hello)
{
    const esp_app_desc_t *app = esp_app_get_description();
    char built[40];
    snprintf(built, sizeof built, "%s %s", app->date, app->time);
    cJSON_AddStringToObject(hello, "fw_version", app->version);
    cJSON_AddStringToObject(hello, "fw_build", built);
    cJSON_AddStringToObject(hello, "idf", app->idf_ver);
    cJSON_AddStringToObject(hello, "esp_dl", ESP_DL_VER);
    cJSON_AddStringToObject(hello, "esp32_camera", ESP32_CAMERA_VER);
    cJSON_AddStringToObject(hello, "pedestrian_detect", PEDESTRIAN_DETECT_VER);
    cJSON_AddStringToObject(hello, "model", "pico_s8_v1_s3");
}

static void add_hardware(cJSON *hello)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    uint32_t flash_bytes = 0;
    esp_flash_get_size(NULL, &flash_bytes);
    char revision[8], sensor_pid[8];
    snprintf(revision, sizeof revision, "v%d.%d", chip.revision / 100, chip.revision % 100);
    snprintf(sensor_pid, sizeof sensor_pid, "0x%04x", camera_sensor_pid());
    cJSON_AddStringToObject(hello, "chip", "esp32s3");
    cJSON_AddStringToObject(hello, "chip_rev", revision);
    cJSON_AddNumberToObject(hello, "flash_bytes", flash_bytes);
    cJSON_AddNumberToObject(hello, "psram_bytes", esp_psram_get_size());
    cJSON_AddStringToObject(hello, "sensor_pid", sensor_pid);
}

/** Boxes are in pixels of the frame they came from ("frame_px"), or of the still in inference_only. */
cJSON *hello_json()
{
    cJSON *hello = cJSON_CreateObject();
    cJSON_AddNumberToObject(hello, "proto_version", PROTO_VERSION);
    add_device_identity(hello);
    add_software_versions(hello);
    add_hardware(hello);
    cJSON_AddNumberToObject(hello, "grid_cols", GRID_COLS);
    cJSON_AddNumberToObject(hello, "grid_rows", GRID_ROWS);
    cJSON_AddStringToObject(hello, "box_coords", "frame_px");
    cJSON_AddStringToObject(hello, "reset_reason", reset_reason_str());
    cJSON_AddStringToObject(hello, "wifi", wifi_state());
    cJSON_AddStringToObject(hello, "ip", wifi_ip());
    settings_to_json(cJSON_AddObjectToObject(hello, "config"));
    return hello;
}

static bool send_jpeg_frame(client_connection_t *client, const bundle_t *bundle)
{
    jpeg_frame_header_t header = {bundle->width, bundle->height, settings_get().jpeg_quality, {0, 0, 0},
                                  bundle->t_capture_us};
    return proto_send(client->sock, &client->seq, REC_JPEG_FRAME, bundle->frame_id, &header, sizeof header,
                      bundle->jpeg_buffer, bundle->jpeg_len);
}

static bool send_detections(client_connection_t *client, const bundle_t *bundle)
{
    uint8_t payload[sizeof(detections_header_t) + MAX_BOXES_PER_FRAME * sizeof(wire_box_t)];
    detections_header_t header = {COORDINATES_FRAME_PIXELS, bundle->box_count, bundle->width, bundle->height, 0};
    memcpy(payload, &header, sizeof header);
    for (int i = 0; i < bundle->box_count; i++) {
        const box_t &box = bundle->boxes[i];
        wire_box_t wire = {box.x1, box.y1, box.x2, box.y2, box.score, CLASS_PERSON, {0, 0, 0}};
        memcpy(payload + sizeof header + i * sizeof wire, &wire, sizeof wire);
    }
    return proto_send(client->sock, &client->seq, REC_DETECTIONS, bundle->frame_id, payload,
                      sizeof header + bundle->box_count * sizeof(wire_box_t));
}

static bool send_frame_timing(client_connection_t *client, const bundle_t *bundle)
{
    cJSON *timing = cJSON_CreateObject();
    cJSON_AddNumberToObject(timing, "frame_id", bundle->frame_id);
    for (int stage = 0; stage < ST_COUNT; stage++)
        if (bundle->stage_us[stage] != STAGE_NOT_MEASURED)
            cJSON_AddNumberToObject(timing, STAGE_NAMES[stage], bundle->stage_us[stage]);
    char *text = cJSON_PrintUnformatted(timing);
    cJSON_Delete(timing);
    bool sent = !text || proto_send(client->sock, &client->seq, REC_FRAME_TIMING, bundle->frame_id, text, strlen(text));
    cJSON_free(text);
    return sent;
}

static bool send_bundle(client_connection_t *client, const bundle_t *bundle, bool with_frame_timing)
{
    if (bundle->jpeg_len && !send_jpeg_frame(client, bundle)) return false;
    if (!send_detections(client, bundle)) return false;
    return !with_frame_timing || send_frame_timing(client, bundle);
}

static bool send_grid(client_connection_t *client)
{
    static uint8_t payload[20 + GRID_COLS * GRID_ROWS * sizeof(uint32_t)];
    size_t length = grid_payload(payload, sizeof payload);
    return proto_send(client->sock, &client->seq, REC_PRESENCE_GRID, 0, payload, length);
}

static const char *severity_name(event_severity_t severity)
{
    if (severity == EV_ERROR) return "error";
    if (severity == EV_WARN) return "warn";
    return "info";
}

static bool send_pending_events(client_connection_t *client)
{
    event_t event;
    while (event_pop(&event)) {
        cJSON *json = cJSON_CreateObject();
        cJSON_AddStringToObject(json, "severity", severity_name(event.severity));
        cJSON_AddStringToObject(json, "code", event.code);
        cJSON_AddStringToObject(json, "text", event.text);
        cJSON_AddNumberToObject(json, "t_event_us", (double)event.t_us);
        if (!send_json(client, REC_EVENT, json)) return false;
    }
    return true;
}

static void on_incoming_record(uint8_t type, const uint8_t *payload, uint32_t len, void *context)
{
    if (type == REC_COMMAND) command_dispatch(payload, len, (client_connection_t *)context);
}

/** Reads whatever the client sent without blocking. Returns false if the client closed or failed. */
static bool receive_commands(client_connection_t *client, proto_receiver_t *receiver)
{
    uint8_t incoming[256];
    for (;;) {
        int received = recv(client->sock, incoming, sizeof incoming, MSG_DONTWAIT);
        if (received == 0) return false;
        if (received < 0) return errno == EAGAIN || errno == EWOULDBLOCK;
        proto_receiver_feed(receiver, incoming, received, on_incoming_record, client);
    }
}

/**
 * Once per metrics interval: thermal check, METRICS window, drop-burst event, and a copy of the
 * window on serial every SERIAL_METRICS_EVERY_S. Works without a client (sock == NO_SOCKET).
 */
static bool run_metrics_tick(client_connection_t *client, int64_t &next_tick_us)
{
    static int64_t next_serial_us = 0;
    int64_t now_us = esp_timer_get_time();
    if (now_us < next_tick_us) return true;
    app_cfg_t cfg = settings_get();
    next_tick_us = now_us + (int64_t)cfg.metrics_interval_ms * 1000;
    thermal_check(cfg.thermal_warn_c);
    cJSON *metrics = metrics_window();
    int dropped = cJSON_GetObjectItem(metrics, "dropped_in_window")->valueint;
    if (dropped >= DROP_BURST_EVENT_THRESHOLD)
        event_post(EV_WARN, "queue_drop_burst", "%d bundles dropped in window", dropped);
    if (now_us >= next_serial_us) {
        next_serial_us = now_us + SERIAL_METRICS_EVERY_S * 1000000LL;
        char *text = cJSON_PrintUnformatted(metrics);
        if (text) ESP_LOGI(TAG, "METRICS %s", text);
        cJSON_free(text);
    }
    return send_json(client, REC_METRICS, metrics);
}

static int open_server_socket()
{
    int server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    int enable = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enable, sizeof enable);
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(TCP_PORT);
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(server, (sockaddr *)&address, sizeof address) != 0 || listen(server, 1) != 0) return NO_SOCKET;
    return server;
}

static void recycle_ready_bundles()
{
    bundle_t *bundle;
    while (xQueueReceive(g_ready_bundles, &bundle, 0) == pdTRUE) {
        xQueueSend(g_free_bundles, &bundle, 0);
        g_counters.no_client_discards++;
    }
}

static int wait_for_client(int server, int64_t &next_tick_us)
{
    client_connection_t nobody = {NO_SOCKET, 0, false, false, false};
    for (;;) {
        fd_set readable;
        FD_ZERO(&readable);
        FD_SET(server, &readable);
        timeval poll_timeout = {0, ACCEPT_POLL_US};
        int client_sock = NO_SOCKET;
        if (select(server + 1, &readable, nullptr, nullptr, &poll_timeout) > 0)
            client_sock = accept(server, nullptr, nullptr);
        recycle_ready_bundles();
        run_metrics_tick(&nobody, next_tick_us);
        if (client_sock != NO_SOCKET) return client_sock;
    }
}

static void configure_client_socket(int sock)
{
    timeval send_timeout = {SOCKET_SEND_TIMEOUT_S, 0};
    int enable = 1;
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &send_timeout, sizeof send_timeout);
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &enable, sizeof enable);
}

/** Sends the next ready bundle, if one arrives within BUNDLE_WAIT_MS. */
static bool send_next_bundle(client_connection_t *client, const app_cfg_t &cfg, uint32_t &bundles_since_grid)
{
    bundle_t *bundle;
    if (xQueueReceive(g_ready_bundles, &bundle, pdMS_TO_TICKS(BUNDLE_WAIT_MS)) != pdTRUE) return true;
    int64_t started_us = esp_timer_get_time();
    bool sent = send_bundle(client, bundle, cfg.metrics_verbose);
    if (sent) {
        metrics_add(ST_SEND, esp_timer_get_time() - started_us);
        g_counters.bundles_sent++;
        bundles_since_grid++;
    }
    xQueueSend(g_free_bundles, &bundle, 0);
    return sent;
}

static bool send_requested_records(client_connection_t *client, const app_cfg_t &cfg, uint32_t &bundles_since_grid)
{
    bool streaming = cfg.mode == MODE_METADATA_STREAM || cfg.mode == MODE_FULL_STREAM;
    if (client->grid_requested || (streaming && bundles_since_grid >= cfg.grid_every_n)) {
        client->grid_requested = false;
        bundles_since_grid = 0;
        if (!send_grid(client)) return false;
    }
    if (client->hello_requested) {
        client->hello_requested = false;
        if (!send_json(client, REC_HELLO, hello_json())) return false;
    }
    return true;
}

static void reboot_after(client_connection_t *client)
{
    close(client->sock);
    vTaskDelay(pdMS_TO_TICKS(REBOOT_DELAY_MS));
    esp_restart();
}

static void serve_client(int sock, int64_t &next_tick_us)
{
    static proto_receiver_t receiver;
    client_connection_t client = {sock, 0, false, false, false};
    configure_client_socket(sock);
    receiver.length = 0;
    g_counters.clients++;
    event_post(EV_INFO, "client_connected", "count=%u", (unsigned)g_counters.clients);

    uint32_t bundles_since_grid = 0;
    bool connected = send_json(&client, REC_HELLO, hello_json());
    while (connected) {
        app_cfg_t cfg = settings_get();
        connected = send_next_bundle(&client, cfg, bundles_since_grid) && send_pending_events(&client) &&
                    receive_commands(&client, &receiver) && send_requested_records(&client, cfg, bundles_since_grid) &&
                    run_metrics_tick(&client, next_tick_us);
        if (client.reboot_requested) reboot_after(&client);
    }
    event_post(EV_WARN, "client_lost", "errno=%d", errno);
    close(sock);
}

void net_task(void *)
{
    int server = open_server_socket();
    if (server == NO_SOCKET) {
        event_post(EV_ERROR, "net_listen_failed", "errno=%d", errno);
        vTaskDelete(nullptr);
    }
    int64_t next_tick_us = 0;
    for (;;) serve_client(wait_for_client(server, next_tick_us), next_tick_us);
}
