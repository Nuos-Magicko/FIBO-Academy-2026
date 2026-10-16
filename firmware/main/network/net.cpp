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
#include <array>
#include <cstring>
#include "component_versions.h"
#include "network/net.h"
#include "config/settings.h"
#include "network/commands.h"
#include "network/protocol.h"
#include "network/wifi.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "telemetry/thermal.h"
#include "utils/device.h"
#include "utils/units.h"
#include "vision/bundle.h"
#include "vision/camera.h"
#include "vision/grid.h"
#include "esp_app_desc.h"
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
static constexpr int ONE_PENDING_CLIENT = 1;
static constexpr int SOCKET_OPTION_ON = 1;
static constexpr TickType_t NO_WAIT = 0;
static constexpr uint8_t COORDINATES_FRAME_PIXELS = 0;
static constexpr uint8_t CLASS_PERSON = 0;
static constexpr uint32_t NO_FRAME_ID = 0;
static constexpr size_t INCOMING_CHUNK_BYTES = 256;
/** "aa:bb:cc:dd:ee:ff" plus the closing '\0'. */
static constexpr size_t MAC_TEXT_BYTES = 18;
/** "gmt-ddeeff" plus the closing '\0', with room to spare. */
static constexpr size_t DEVICE_ID_BYTES = 16;
static constexpr size_t BUILD_TIME_TEXT_BYTES = 40;
/** "0x3660" plus the closing '\0', with room to spare. */
static constexpr size_t SENSOR_PID_TEXT_BYTES = 8;

struct __attribute__((packed)) jpeg_frame_header_t {
    uint16_t width;
    uint16_t height;
    uint8_t quality;
    uint8_t pad[3];
    int64_t t_capture_us;
};

struct __attribute__((packed)) detections_header_t {
    uint8_t coordinate_space;
    uint8_t count;
    uint16_t width;
    uint16_t height;
    uint16_t pad;
};

struct __attribute__((packed)) wire_box_t {
    int16_t x1;
    int16_t y1;
    int16_t x2;
    int16_t y2;
    float score;
    uint8_t class_id;
    uint8_t pad[3];
};

/**
 * When the JSON cannot be printed (out of memory) the record is skipped but the connection kept.
 * With no client connected (sock == NO_SOCKET) nothing is sent.
 */
bool send_json(client_connection_t *client, record_type_t type, cJSON *obj)
{
    char *text = cJSON_PrintUnformatted(obj);
    cJSON_Delete(obj);
    if (text == nullptr) {
        return true;
    }
    bool sent = true;
    if (client->sock != NO_SOCKET) {
        sent = proto_send(client->sock, &client->seq, type, NO_FRAME_ID, text, strlen(text));
    }
    cJSON_free(text);
    return sent;
}

static void add_device_identity(cJSON *hello)
{
    mac_address_t mac = device_mac();
    char mac_text[MAC_TEXT_BYTES];
    char device_id[DEVICE_ID_BYTES];
    snprintf(mac_text, sizeof(mac_text), "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4],
             mac[5]);
    snprintf(device_id, sizeof(device_id), "gmt-%02x%02x%02x", mac[3], mac[4], mac[5]);
    cJSON_AddStringToObject(hello, "device_id", device_id);
    cJSON_AddStringToObject(hello, "mac", mac_text);
}

/** fw_version is `git describe` of the tree the firmware was built from. */
static void add_software_versions(cJSON *hello)
{
    const esp_app_desc_t *app = esp_app_get_description();
    char built[BUILD_TIME_TEXT_BYTES];
    snprintf(built, sizeof(built), "%s %s", app->date, app->time);
    cJSON_AddStringToObject(hello, "fw_version", app->version);
    cJSON_AddStringToObject(hello, "fw_build", built);
    cJSON_AddStringToObject(hello, "idf", app->idf_ver);
    cJSON_AddStringToObject(hello, "esp_dl", ESP_DL_VERSION);
    cJSON_AddStringToObject(hello, "esp32_camera", ESP32_CAMERA_VERSION);
    cJSON_AddStringToObject(hello, "pedestrian_detect", PEDESTRIAN_DETECT_VERSION);
    cJSON_AddStringToObject(hello, "model", "pico_s8_v1_s3");
}

static void add_hardware(cJSON *hello)
{
    uint32_t flash_bytes = 0;
    esp_flash_get_size(nullptr, &flash_bytes);
    char revision[CHIP_REVISION_TEXT_BYTES];
    chip_revision_text(revision, sizeof(revision));
    char sensor_pid[SENSOR_PID_TEXT_BYTES];
    snprintf(sensor_pid, sizeof(sensor_pid), "0x%04x", camera_sensor_pid());
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
    jpeg_frame_header_t header = {};
    header.width = bundle->width;
    header.height = bundle->height;
    header.quality = settings_get().jpeg_quality;
    header.t_capture_us = bundle->t_capture_us;
    return proto_send(client->sock, &client->seq, record_type_t::jpeg_frame, bundle->frame_id, &header,
                      sizeof(header), bundle->jpeg_buffer, bundle->jpeg_len);
}

static bool send_detections(client_connection_t *client, const bundle_t *bundle)
{
    std::array<uint8_t, sizeof(detections_header_t) + MAX_BOXES_PER_FRAME * sizeof(wire_box_t)> payload;
    detections_header_t header = {};
    header.coordinate_space = COORDINATES_FRAME_PIXELS;
    header.count = bundle->box_count;
    header.width = bundle->width;
    header.height = bundle->height;
    memcpy(payload.data(), &header, sizeof(header));
    for (int index = 0; index < bundle->box_count; index++) {
        const box_t &box = bundle->boxes[index];
        wire_box_t wire = {};
        wire.x1 = box.x1;
        wire.y1 = box.y1;
        wire.x2 = box.x2;
        wire.y2 = box.y2;
        wire.score = box.score;
        wire.class_id = CLASS_PERSON;
        memcpy(payload.data() + sizeof(header) + index * sizeof(wire), &wire, sizeof(wire));
    }
    size_t length = sizeof(header) + bundle->box_count * sizeof(wire_box_t);
    return proto_send(client->sock, &client->seq, record_type_t::detections, bundle->frame_id, payload.data(),
                      length);
}

static bool send_frame_timing(client_connection_t *client, const bundle_t *bundle)
{
    cJSON *timing = cJSON_CreateObject();
    cJSON_AddNumberToObject(timing, "frame_id", bundle->frame_id);
    for (stage_t stage : ALL_STAGES) {
        if (bundle->stage_us[stage] != STAGE_NOT_MEASURED) {
            cJSON_AddNumberToObject(timing, stage_name(stage), bundle->stage_us[stage]);
        }
    }
    char *text = cJSON_PrintUnformatted(timing);
    cJSON_Delete(timing);
    if (text == nullptr) {
        return true;
    }
    bool sent = proto_send(client->sock, &client->seq, record_type_t::frame_timing, bundle->frame_id, text,
                           strlen(text));
    cJSON_free(text);
    return sent;
}

static bool send_bundle(client_connection_t *client, const bundle_t *bundle, bool with_frame_timing)
{
    if (bundle->jpeg_len > 0) {
        if (!send_jpeg_frame(client, bundle)) {
            return false;
        }
    }
    if (!send_detections(client, bundle)) {
        return false;
    }
    if (with_frame_timing) {
        return send_frame_timing(client, bundle);
    }
    return true;
}

static bool send_grid(client_connection_t *client)
{
    static std::array<uint8_t, GRID_PAYLOAD_BYTES> payload;
    size_t length = grid_payload(payload.data(), payload.size());
    return proto_send(client->sock, &client->seq, record_type_t::presence_grid, NO_FRAME_ID, payload.data(), length);
}

static bool send_pending_events(client_connection_t *client)
{
    event_t event;
    while (event_pop(&event)) {
        cJSON *json = cJSON_CreateObject();
        cJSON_AddStringToObject(json, "severity", severity_name(event.severity));
        cJSON_AddStringToObject(json, "code", event.code);
        cJSON_AddStringToObject(json, "text", event.text);
        cJSON_AddNumberToObject(json, "t_event_us", static_cast<double>(event.t_us));
        if (!send_json(client, record_type_t::event, json)) {
            return false;
        }
    }
    return true;
}

static void on_incoming_record(record_type_t type, const uint8_t *payload, uint32_t len, void *context)
{
    if (type == record_type_t::command) {
        client_connection_t *client = static_cast<client_connection_t *>(context);
        command_dispatch(payload, len, client);
    }
}

/** Reads whatever the client sent without blocking. Returns false if the client closed or failed. */
static bool receive_commands(client_connection_t *client, proto_receiver_t *receiver)
{
    std::array<uint8_t, INCOMING_CHUNK_BYTES> incoming;
    while (true) {
        int received = recv(client->sock, incoming.data(), incoming.size(), MSG_DONTWAIT);
        if (received == 0) {
            return false;
        }
        if (received < 0) {
            bool nothing_to_read = (errno == EAGAIN || errno == EWOULDBLOCK);
            return nothing_to_read;
        }
        proto_receiver_feed(receiver, incoming.data(), received, on_incoming_record, client);
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
    if (now_us < next_tick_us) {
        return true;
    }
    app_cfg_t settings = settings_get();
    next_tick_us = now_us + settings.metrics_interval_ms * MICROSECONDS_PER_MILLISECOND;
    thermal_check(settings.thermal_warn_c);
    cJSON *metrics = metrics_window();
    int dropped = cJSON_GetObjectItem(metrics, "dropped_in_window")->valueint;
    if (dropped >= DROP_BURST_EVENT_THRESHOLD) {
        event_post(event_severity_t::warn, "queue_drop_burst", "%d bundles dropped in window", dropped);
    }
    if (now_us >= next_serial_us) {
        next_serial_us = now_us + SERIAL_METRICS_EVERY_S * MICROSECONDS_PER_SECOND;
        char *text = cJSON_PrintUnformatted(metrics);
        if (text != nullptr) {
            ESP_LOGI(TAG, "METRICS %s", text);
        }
        cJSON_free(text);
    }
    return send_json(client, record_type_t::metrics, metrics);
}

static int open_server_socket()
{
    int server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    int enable = SOCKET_OPTION_ON;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enable, sizeof(enable));
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(TCP_PORT);
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    sockaddr *generic_address = reinterpret_cast<sockaddr *>(&address);
    if (bind(server, generic_address, sizeof(address)) != 0) {
        return NO_SOCKET;
    }
    if (listen(server, ONE_PENDING_CLIENT) != 0) {
        return NO_SOCKET;
    }
    return server;
}

static void recycle_ready_bundles()
{
    bundle_t *bundle;
    while (xQueueReceive(g_ready_bundles, &bundle, NO_WAIT) == pdTRUE) {
        xQueueSend(g_free_bundles, &bundle, NO_WAIT);
        g_counters.no_client_discards++;
    }
}

static client_connection_t new_connection(int sock)
{
    client_connection_t connection;
    connection.sock = sock;
    connection.seq = 0;
    connection.grid_requested = false;
    connection.hello_requested = false;
    connection.reboot_requested = false;
    return connection;
}

static int wait_for_client(int server, int64_t &next_tick_us)
{
    client_connection_t nobody = new_connection(NO_SOCKET);
    while (true) {
        fd_set readable;
        FD_ZERO(&readable);
        FD_SET(server, &readable);
        timeval poll_timeout = {};
        poll_timeout.tv_sec = 0;
        poll_timeout.tv_usec = ACCEPT_POLL_US;
        int client_sock = NO_SOCKET;
        if (select(server + 1, &readable, nullptr, nullptr, &poll_timeout) > 0) {
            client_sock = accept(server, nullptr, nullptr);
        }
        recycle_ready_bundles();
        run_metrics_tick(&nobody, next_tick_us);
        if (client_sock != NO_SOCKET) {
            return client_sock;
        }
    }
}

static void configure_client_socket(int sock)
{
    timeval send_timeout = {};
    send_timeout.tv_sec = SOCKET_SEND_TIMEOUT_S;
    send_timeout.tv_usec = 0;
    int enable = SOCKET_OPTION_ON;
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &send_timeout, sizeof(send_timeout));
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &enable, sizeof(enable));
}

/** Sends the next ready bundle, if one arrives within BUNDLE_WAIT_MS. */
static bool send_next_bundle(client_connection_t *client, const app_cfg_t &settings, uint32_t &bundles_since_grid)
{
    bundle_t *bundle;
    if (xQueueReceive(g_ready_bundles, &bundle, pdMS_TO_TICKS(BUNDLE_WAIT_MS)) != pdTRUE) {
        return true;
    }
    int64_t started_us = esp_timer_get_time();
    bool sent = send_bundle(client, bundle, settings.metrics_verbose);
    if (sent) {
        metrics_add(stage_t::send, esp_timer_get_time() - started_us);
        g_counters.bundles_sent++;
        bundles_since_grid++;
    }
    xQueueSend(g_free_bundles, &bundle, NO_WAIT);
    return sent;
}

static bool send_requested_records(client_connection_t *client, const app_cfg_t &settings,
                                   uint32_t &bundles_since_grid)
{
    bool streaming = (settings.mode == app_mode_t::metadata_stream || settings.mode == app_mode_t::full_stream);
    bool grid_is_due = (streaming && bundles_since_grid >= settings.grid_every_n);
    if (client->grid_requested || grid_is_due) {
        client->grid_requested = false;
        bundles_since_grid = 0;
        if (!send_grid(client)) {
            return false;
        }
    }
    if (client->hello_requested) {
        client->hello_requested = false;
        if (!send_json(client, record_type_t::hello, hello_json())) {
            return false;
        }
    }
    return true;
}

static void reboot_after(client_connection_t *client)
{
    close(client->sock);
    vTaskDelay(pdMS_TO_TICKS(REBOOT_DELAY_MS));
    esp_restart();
}

/** One pass of the client loop. Returns false as soon as one step finds the connection broken. */
static bool serve_one_round(client_connection_t *client, proto_receiver_t *receiver, uint32_t &bundles_since_grid,
                            int64_t &next_tick_us)
{
    app_cfg_t settings = settings_get();
    if (!send_next_bundle(client, settings, bundles_since_grid)) {
        return false;
    }
    if (!send_pending_events(client)) {
        return false;
    }
    if (!receive_commands(client, receiver)) {
        return false;
    }
    if (!send_requested_records(client, settings, bundles_since_grid)) {
        return false;
    }
    if (!run_metrics_tick(client, next_tick_us)) {
        return false;
    }
    return true;
}

static void serve_client(int sock, int64_t &next_tick_us)
{
    static proto_receiver_t receiver;
    client_connection_t client = new_connection(sock);
    configure_client_socket(sock);
    receiver.length = 0;
    g_counters.clients++;
    event_post(event_severity_t::info, "client_connected", "count=%u", static_cast<unsigned>(g_counters.clients));

    uint32_t bundles_since_grid = 0;
    bool connected = send_json(&client, record_type_t::hello, hello_json());
    while (connected) {
        connected = serve_one_round(&client, &receiver, bundles_since_grid, next_tick_us);
        if (client.reboot_requested) {
            reboot_after(&client);
        }
    }
    event_post(event_severity_t::warn, "client_lost", "errno=%d", errno);
    close(sock);
}

void net_task(void *)
{
    int server = open_server_socket();
    if (server == NO_SOCKET) {
        event_post(event_severity_t::error, "net_listen_failed", "errno=%d", errno);
        vTaskDelete(nullptr);
    }
    int64_t next_tick_us = 0;
    while (true) {
        int client_sock = wait_for_client(server, next_tick_us);
        serve_client(client_sock, next_tick_us);
    }
}
