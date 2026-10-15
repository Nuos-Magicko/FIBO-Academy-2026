// Die temperature (ESP32-S3 internal sensor). Warning EVENT only: no automatic throttling.
#include <cmath>
#include "app.h"
#include "driver/temperature_sensor.h"
#include "freertos/semphr.h"

static temperature_sensor_handle_t s_ts;
static SemaphoreHandle_t s_mtx;
static bool s_warned;

void thermal_init()
{
    s_mtx = xSemaphoreCreateMutex();
    temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 100);
    if (temperature_sensor_install(&cfg, &s_ts) != ESP_OK || temperature_sensor_enable(s_ts) != ESP_OK) s_ts = nullptr;
}

float thermal_read_c()
{
    float t = NAN;
    if (!s_ts) return t;
    xSemaphoreTake(s_mtx, portMAX_DELAY);
    temperature_sensor_get_celsius(s_ts, &t);
    xSemaphoreGive(s_mtx);
    return t;
}

void thermal_check(float warn_c)
{
    float t = thermal_read_c();
    if (std::isnan(t)) return;
    if (!s_warned && t >= warn_c) {
        s_warned = true;
        event_post(EV_WARN, "thermal_warning", "die %.1fC >= %.1fC", t, warn_c);
    } else if (s_warned && t < warn_c - THERMAL_HYSTERESIS_C) {
        s_warned = false;
        event_post(EV_INFO, "thermal_ok", "die %.1fC < %.1fC", t, warn_c - THERMAL_HYSTERESIS_C);
    }
}
