/**
 * @file events.h
 * EVENT messages: short notes from the board such as "mode_changed" or "thermal_warning".
 */
#pragma once
#include <cstdint>

enum event_severity_t : uint8_t { EV_INFO, EV_WARN, EV_ERROR };

struct event_t {
    int64_t t_us;
    event_severity_t severity;
    char code[24];
    char text[96];
};

void events_init();
/**
 * Logs the event on serial and queues it for the TCP client. When the queue is full the oldest
 * event is dropped and counted in events_dropped.
 */
void event_post(event_severity_t severity, const char *code, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));
bool event_pop(event_t *out);
