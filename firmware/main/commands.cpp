// COMMAND (PC -> device), JSON payloads:
//   {"cmd":"ping","t_host_us":N}            -> PONG {"t_host_us":N,"t_dev_us":M}
//   {"cmd":"set","values":{"mode":"metadata_stream","jpeg_quality":10,...}}  (see settings.cpp keys)
//   {"cmd":"reset_grid"} | {"cmd":"get_grid"} | {"cmd":"get_hello"} | {"cmd":"save"} | {"cmd":"reboot"}
#include <cstring>
#include "app.h"
#include "esp_timer.h"
#include "protocol.h"

static void reply(bool ok, const char *cmd, const char *text)
{
    if (ok) g_cnt.cmd_ok++;
    else g_cnt.cmd_err++;
    event_post(ok ? EV_INFO : EV_WARN, ok ? "cmd_ok" : "cmd_error", "%s: %s", cmd, text);
}

void command_dispatch(const uint8_t *p, uint32_t len, conn_t *c)
{
    cJSON *j = cJSON_ParseWithLength((const char *)p, len);
    const cJSON *cmd = cJSON_GetObjectItem(j, "cmd");
    if (!cJSON_IsString(cmd)) {
        reply(false, "?", "payload must be JSON with a string 'cmd'");
        cJSON_Delete(j);
        return;
    }
    const char *name = cmd->valuestring;
    if (!strcmp(name, "ping")) {
        cJSON *r = cJSON_CreateObject();
        const cJSON *th = cJSON_GetObjectItem(j, "t_host_us");
        cJSON_AddItemToObject(r, "t_host_us", th ? cJSON_Duplicate(th, false) : cJSON_CreateNull());
        cJSON_AddNumberToObject(r, "t_dev_us", (double)esp_timer_get_time());
        send_json(c, REC_PONG, r);
        g_cnt.cmd_ok++;
    } else if (!strcmp(name, "set")) {
        char err[96];
        if (settings_set(cJSON_GetObjectItem(j, "values"), err, sizeof err)) {
            reply(true, name, "applied (wifi keys need save + reboot)");
            c->want_hello = true; // HELLO carries the full configuration
        } else {
            reply(false, name, err);
        }
    } else if (!strcmp(name, "reset_grid")) {
        grid_clear();
        reply(true, name, "grid cleared");
        c->want_grid = true;
    } else if (!strcmp(name, "get_grid")) {
        c->want_grid = true;
        g_cnt.cmd_ok++;
    } else if (!strcmp(name, "get_hello")) {
        c->want_hello = true;
        g_cnt.cmd_ok++;
    } else if (!strcmp(name, "save")) {
        bool ok = settings_save();
        reply(ok, name, ok ? "saved to NVS" : "NVS write failed");
    } else if (!strcmp(name, "reboot")) {
        reply(true, name, "rebooting");
        c->reboot = true;
    } else {
        reply(false, name, "unknown cmd");
    }
    cJSON_Delete(j);
}
