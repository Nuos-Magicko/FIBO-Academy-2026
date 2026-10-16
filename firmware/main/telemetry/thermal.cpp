/**
 * @file thermal.cpp
 * Die temperature from the ESP32-S3 internal sensor. Warns with an EVENT; never throttles by itself.
 */
#include <cmath>
#include "telemetry/thermal.h"
#include "config/config.h"
#include "telemetry/events.h"
#include "driver/temperature_sensor.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static constexpr int SENSOR_RANGE_MIN_C = 20;
static constexpr int SENSOR_RANGE_MAX_C = 100;

static temperature_sensor_handle_t s_sensor;
static SemaphoreHandle_t s_lock;
static bool s_warning_active;

void thermal_init()
{
    s_lock = xSemaphoreCreateMutex();
    temperature_sensor_config_t config = TEMPERATURE_SENSOR_CONFIG_DEFAULT(SENSOR_RANGE_MIN_C, SENSOR_RANGE_MAX_C);
    if (temperature_sensor_install(&config, &s_sensor) != ESP_OK) {
        s_sensor = nullptr;
        return;
    }
    if (temperature_sensor_enable(s_sensor) != ESP_OK) {
        s_sensor = nullptr;
    }
}

float thermal_read_c()
{
    float celsius = NAN;
    if (s_sensor == nullptr) {
        return celsius;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    temperature_sensor_get_celsius(s_sensor, &celsius);
    xSemaphoreGive(s_lock);
    return celsius;
}

void thermal_check(float warn_c)
{
    float celsius = thermal_read_c();
    if (std::isnan(celsius)) {
        return;
    }
    const float clear_c = warn_c - THERMAL_HYSTERESIS_C;
    if (!s_warning_active && celsius >= warn_c) {
        s_warning_active = true;
        event_post(event_severity_t::warn, "thermal_warning", "die %.1fC >= %.1fC", celsius, warn_c);
    } else if (s_warning_active && celsius < clear_c) {
        s_warning_active = false;
        event_post(event_severity_t::info, "thermal_ok", "die %.1fC < %.1fC", celsius, clear_c);
    }
}
