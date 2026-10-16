/**
 * @file commands.cpp
 * COMMAND records (PC to device), JSON payloads:
 *
 *   {"cmd":"ping","t_host_us":N}           replies PONG {"t_host_us":N,"t_dev_us":M}
 *   {"cmd":"set","values":{...}}           keys as in settings.cpp; replies with a new HELLO
 *   {"cmd":"reset_grid"}  {"cmd":"get_grid"}  {"cmd":"get_hello"}  {"cmd":"save"}  {"cmd":"reboot"}
 */
#include <algorithm>
#include <cstring>
#include <iterator>
#include "network/commands.h"
#include "config/settings.h"
#include "network/net.h"
#include "network/protocol.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "vision/grid.h"
#include "esp_timer.h"

static void reply(bool ok, const char *cmd, const char *text)
{
    if (ok) g_counters.cmd_ok++;
    else g_counters.cmd_err++;
    event_post(ok ? EV_INFO : EV_WARN, ok ? "cmd_ok" : "cmd_error", "%s: %s", cmd, text);
}

static void handle_ping(const cJSON *request, client_connection_t *client)
{
    cJSON *pong = cJSON_CreateObject();
    const cJSON *t_host_us = cJSON_GetObjectItem(request, "t_host_us");
    cJSON_AddItemToObject(pong, "t_host_us", t_host_us ? cJSON_Duplicate(t_host_us, false) : cJSON_CreateNull());
    cJSON_AddNumberToObject(pong, "t_dev_us", (double)esp_timer_get_time());
    send_json(client, REC_PONG, pong);
    g_counters.cmd_ok++;
}

static void handle_set(const cJSON *request, client_connection_t *client)
{
    char err[96];
    if (!settings_set(cJSON_GetObjectItem(request, "values"), err, sizeof err)) {
        reply(false, "set", err);
        return;
    }
    reply(true, "set", "applied (wifi keys need save + reboot)");
    client->hello_requested = true;
}

static void handle_reset_grid(const cJSON *, client_connection_t *client)
{
    grid_clear();
    reply(true, "reset_grid", "grid cleared");
    client->grid_requested = true;
}

static void handle_get_grid(const cJSON *, client_connection_t *client)
{
    client->grid_requested = true;
    g_counters.cmd_ok++;
}

static void handle_get_hello(const cJSON *, client_connection_t *client)
{
    client->hello_requested = true;
    g_counters.cmd_ok++;
}

static void handle_save(const cJSON *, client_connection_t *)
{
    bool saved = settings_save();
    reply(saved, "save", saved ? "saved to NVS" : "NVS write failed");
}

static void handle_reboot(const cJSON *, client_connection_t *client)
{
    reply(true, "reboot", "rebooting");
    client->reboot_requested = true;
}

static const struct {
    const char *name;
    void (*handle)(const cJSON *request, client_connection_t *client);
} COMMANDS[] = {
    {"ping", handle_ping},         {"set", handle_set},   {"reset_grid", handle_reset_grid},
    {"get_grid", handle_get_grid}, {"get_hello", handle_get_hello}, {"save", handle_save},
    {"reboot", handle_reboot},
};

void command_dispatch(const uint8_t *payload, uint32_t len, client_connection_t *client)
{
    cJSON *request = cJSON_ParseWithLength((const char *)payload, len);
    const cJSON *cmd = cJSON_GetObjectItem(request, "cmd");
    if (!cJSON_IsString(cmd)) {
        reply(false, "?", "payload must be JSON with a string 'cmd'");
    } else {
        auto *command = std::find_if(std::begin(COMMANDS), std::end(COMMANDS),
                                     [&](const auto &c) { return !strcmp(c.name, cmd->valuestring); });
        if (command != std::end(COMMANDS)) command->handle(request, client);
        else reply(false, cmd->valuestring, "unknown cmd");
    }
    cJSON_Delete(request);
}
