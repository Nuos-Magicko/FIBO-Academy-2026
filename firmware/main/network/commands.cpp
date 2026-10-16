/**
 * @file commands.cpp
 * COMMAND records (PC to device), JSON payloads:
 *
 *   {"cmd":"ping","t_host_us":N}           replies PONG {"t_host_us":N,"t_dev_us":M}
 *   {"cmd":"set","values":{...}}           keys as in settings.cpp; replies with a new HELLO
 *   {"cmd":"reset_grid"}  {"cmd":"get_grid"}  {"cmd":"get_hello"}  {"cmd":"save"}  {"cmd":"reboot"}
 */
#include <cstring>
#include "network/commands.h"
#include "config/settings.h"
#include "network/net.h"
#include "network/protocol.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "vision/grid.h"
#include "esp_timer.h"

static void reply_ok(const char *cmd, const char *text)
{
    g_counters.cmd_ok++;
    event_post(event_severity_t::info, "cmd_ok", "%s: %s", cmd, text);
}

static void reply_error(const char *cmd, const char *text)
{
    g_counters.cmd_err++;
    event_post(event_severity_t::warn, "cmd_error", "%s: %s", cmd, text);
}

static void handle_ping(const cJSON *request, client_connection_t *client)
{
    cJSON *pong = cJSON_CreateObject();
    const cJSON *t_host_us = cJSON_GetObjectItem(request, "t_host_us");
    if (t_host_us != nullptr) {
        cJSON_AddItemToObject(pong, "t_host_us", cJSON_Duplicate(t_host_us, false));
    } else {
        cJSON_AddItemToObject(pong, "t_host_us", cJSON_CreateNull());
    }
    cJSON_AddNumberToObject(pong, "t_dev_us", static_cast<double>(esp_timer_get_time()));
    send_json(client, record_type_t::pong, pong);
    g_counters.cmd_ok++;
}

static void handle_set(const cJSON *request, client_connection_t *client)
{
    char err[EVENT_TEXT_BYTES];
    if (!settings_set(cJSON_GetObjectItem(request, "values"), err, sizeof(err))) {
        reply_error("set", err);
        return;
    }
    reply_ok("set", "applied (wifi keys need save + reboot)");
    client->hello_requested = true;
}

static void handle_reset_grid(client_connection_t *client)
{
    grid_clear();
    reply_ok("reset_grid", "grid cleared");
    client->grid_requested = true;
}

static void handle_get_grid(client_connection_t *client)
{
    client->grid_requested = true;
    g_counters.cmd_ok++;
}

static void handle_get_hello(client_connection_t *client)
{
    client->hello_requested = true;
    g_counters.cmd_ok++;
}

static void handle_save()
{
    if (settings_save()) {
        reply_ok("save", "saved to NVS");
    } else {
        reply_error("save", "NVS write failed");
    }
}

static void handle_reboot(client_connection_t *client)
{
    reply_ok("reboot", "rebooting");
    client->reboot_requested = true;
}

static void run_command(const char *name, const cJSON *request, client_connection_t *client)
{
    if (strcmp(name, "ping") == 0) {
        handle_ping(request, client);
    } else if (strcmp(name, "set") == 0) {
        handle_set(request, client);
    } else if (strcmp(name, "reset_grid") == 0) {
        handle_reset_grid(client);
    } else if (strcmp(name, "get_grid") == 0) {
        handle_get_grid(client);
    } else if (strcmp(name, "get_hello") == 0) {
        handle_get_hello(client);
    } else if (strcmp(name, "save") == 0) {
        handle_save();
    } else if (strcmp(name, "reboot") == 0) {
        handle_reboot(client);
    } else {
        reply_error(name, "unknown cmd");
    }
}

void command_dispatch(const uint8_t *payload, uint32_t len, client_connection_t *client)
{
    cJSON *request = cJSON_ParseWithLength(reinterpret_cast<const char *>(payload), len);
    const cJSON *cmd = cJSON_GetObjectItem(request, "cmd");
    if (cJSON_IsString(cmd)) {
        run_command(cmd->valuestring, request, client);
    } else {
        reply_error("?", "payload must be JSON with a string 'cmd'");
    }
    cJSON_Delete(request);
}
