/**
 * @file events.cpp
 * EVENT records: logged on serial at once and queued for the TCP client (oldest dropped when full).
 */
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include "telemetry/events.h"
#include "config/config.h"
#include "telemetry/metrics.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

static const char *TAG = "event";
static constexpr TickType_t NO_WAIT = 0;
static QueueHandle_t s_pending_events;

void events_init()
{
    s_pending_events = xQueueCreate(EVENT_QUEUE_DEPTH, sizeof(event_t));
}

const char *severity_name(event_severity_t severity)
{
    switch (severity) {
    case event_severity_t::error:
        return "error";
    case event_severity_t::warn:
        return "warn";
    case event_severity_t::info:
        return "info";
    }
    return "info";
}

static esp_log_level_t log_level_of(event_severity_t severity)
{
    switch (severity) {
    case event_severity_t::error:
        return ESP_LOG_ERROR;
    case event_severity_t::warn:
        return ESP_LOG_WARN;
    case event_severity_t::info:
        return ESP_LOG_INFO;
    }
    return ESP_LOG_INFO;
}

static void enqueue_dropping_oldest(const event_t &event)
{
    if (xQueueSend(s_pending_events, &event, NO_WAIT) == pdTRUE) {
        return;
    }
    event_t oldest;
    xQueueReceive(s_pending_events, &oldest, NO_WAIT);
    g_counters.events_dropped++;
    xQueueSend(s_pending_events, &event, NO_WAIT);
}

void event_post(event_severity_t severity, const char *code, const char *fmt, ...)
{
    event_t event = {};
    event.t_us = esp_timer_get_time();
    event.severity = severity;
    strlcpy(event.code, code, sizeof(event.code));
    va_list args;
    va_start(args, fmt);
    vsnprintf(event.text, sizeof(event.text), fmt, args);
    va_end(args);
    ESP_LOG_LEVEL(log_level_of(severity), TAG, "EVENT %s %s", event.code, event.text);
    if (s_pending_events != nullptr) {
        enqueue_dropping_oldest(event);
    }
}

bool event_pop(event_t *out)
{
    if (s_pending_events == nullptr) {
        return false;
    }
    return xQueueReceive(s_pending_events, out, NO_WAIT) == pdTRUE;
}
