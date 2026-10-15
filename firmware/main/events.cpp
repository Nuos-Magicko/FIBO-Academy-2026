// EVENT queue: every event is logged on serial immediately and queued for the TCP client.
// Full queue: the oldest event is dropped and counted (events_dropped).
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include "app.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "event";
static QueueHandle_t s_q;

void events_init() { s_q = xQueueCreate(EVENT_QUEUE_DEPTH, sizeof(event_t)); }

void event_post(ev_sev_t sev, const char *code, const char *fmt, ...)
{
    event_t e = {};
    e.t_us = esp_timer_get_time();
    e.sev = sev;
    strlcpy(e.code, code, sizeof e.code);
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(e.text, sizeof e.text, fmt, ap);
    va_end(ap);
    esp_log_level_t lvl = sev == EV_ERROR ? ESP_LOG_ERROR : sev == EV_WARN ? ESP_LOG_WARN : ESP_LOG_INFO;
    ESP_LOG_LEVEL(lvl, TAG, "EVENT %s %s", e.code, e.text);
    if (!s_q) return;
    if (xQueueSend(s_q, &e, 0) != pdTRUE) {
        event_t old;
        xQueueReceive(s_q, &old, 0);
        g_cnt.events_dropped++;
        xQueueSend(s_q, &e, 0);
    }
}

bool event_pop(event_t *out) { return s_q && xQueueReceive(s_q, out, 0) == pdTRUE; }
