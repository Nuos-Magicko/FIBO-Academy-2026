/**
 * @file settings.cpp
 * The settings the board runs with: the defaults from config.h (and wifi_secrets.h), replaced by
 * any settings saved in flash (NVS), and changed from the laptop with the "set" command.
 *
 * wifi_secrets.h is git-ignored and holds WIFI_STA_SSID / WIFI_STA_PASS. An incremental build
 * does not notice the file appearing or disappearing: touch this file or run `idf.py fullclean`.
 */
#include <array>
#include <atomic>
#include <cstdarg>
#include <cstring>
#include "config/settings.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#else
constexpr char WIFI_STA_SSID[] = "";
constexpr char WIFI_STA_PASS[] = "";
#endif

static const char *TAG = "settings";
static constexpr uint32_t SETTINGS_LAYOUT_VERSION = 1;
static constexpr char NVS_NAMESPACE[] = "gmt";
static constexpr char NVS_KEY[] = "cfg";

static app_cfg_t s_current;
static std::atomic<uint32_t> s_generation{0};
static SemaphoreHandle_t s_lock;

static constexpr std::array<const char *, APP_MODE_COUNT> MODE_NAMES = {
    "camera_only", "inference_only", "detect_no_stream", "metadata_stream", "full_stream",
};

struct frame_size_entry_t {
    framesize_t frame_size;
    const char *name;
};

static constexpr std::array<frame_size_entry_t, 4> FRAME_SIZES = {{
    {FRAMESIZE_QQVGA, "QQVGA"},
    {FRAMESIZE_QVGA, "QVGA"},
    {FRAMESIZE_HVGA, "HVGA"},
    {FRAMESIZE_VGA, "VGA"},
}};

const char *mode_name(app_mode_t mode)
{
    size_t index = static_cast<size_t>(mode);
    if (index < MODE_NAMES.size()) {
        return MODE_NAMES[index];
    }
    return "?";
}

const char *frame_size_name(framesize_t frame_size)
{
    for (const frame_size_entry_t &entry : FRAME_SIZES) {
        if (entry.frame_size == frame_size) {
            return entry.name;
        }
    }
    return "?";
}

static app_cfg_t compile_time_defaults()
{
    app_cfg_t config = {};
    config.version = SETTINGS_LAYOUT_VERSION;
    config.mode = DEFAULT_MODE;
    config.frame_size = DEFAULT_FRAME_SIZE;
    config.jpeg_quality = DEFAULT_JPEG_QUALITY;
    config.xclk_hz = DEFAULT_XCLK_HZ;
    config.warmup_frames = DEFAULT_WARMUP_FRAMES;
    config.infer_every_n = DEFAULT_INFER_EVERY_N;
    config.infer_only_runs = DEFAULT_INFER_ONLY_RUNS;
    config.metrics_interval_ms = DEFAULT_METRICS_INTERVAL_MS;
    config.grid_every_n = DEFAULT_GRID_EVERY_N;
    config.score_thr = DEFAULT_SCORE_THRESHOLD;
    config.thermal_warn_c = DEFAULT_THERMAL_WARN_C;
    bool has_station_network = (WIFI_STA_SSID[0] != '\0');
    config.use_soft_ap = !has_station_network;
    strlcpy(config.sta_ssid, WIFI_STA_SSID, sizeof(config.sta_ssid));
    strlcpy(config.sta_pass, WIFI_STA_PASS, sizeof(config.sta_pass));
    return config;
}

/** Copies the settings saved in NVS into *out. Returns false if none are saved (or they are too old). */
static bool load_saved_settings(app_cfg_t *out)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) {
        return false;
    }
    app_cfg_t saved;
    size_t size = sizeof(saved);
    esp_err_t result = nvs_get_blob(nvs, NVS_KEY, &saved, &size);
    nvs_close(nvs);

    if (result != ESP_OK) {
        return false;
    }
    if (size != sizeof(saved)) {
        return false;
    }
    if (saved.version != SETTINGS_LAYOUT_VERSION) {
        return false;
    }
    *out = saved;
    return true;
}

static const char *wifi_mode_name(const app_cfg_t &config)
{
    if (config.use_soft_ap) {
        return "ap";
    }
    return "sta";
}

void settings_init()
{
    s_lock = xSemaphoreCreateMutex();
    s_current = compile_time_defaults();
    if (load_saved_settings(&s_current)) {
        ESP_LOGI(TAG, "loaded overrides from NVS");
    }
    ESP_LOGI(TAG, "mode=%s frame=%s q=%u xclk=%u wifi=%s", mode_name(s_current.mode),
             frame_size_name(s_current.frame_size), s_current.jpeg_quality, static_cast<unsigned>(s_current.xclk_hz),
             wifi_mode_name(s_current));
}

app_cfg_t settings_get()
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_cfg_t copy = s_current;
    xSemaphoreGive(s_lock);
    return copy;
}

uint32_t settings_generation()
{
    return s_generation.load();
}

/** Writes an error message into err and returns false, so a check can end with `return reject(...)`. */
static bool reject(char *err, size_t err_len, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static bool reject(char *err, size_t err_len, const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    vsnprintf(err, err_len, fmt, args);
    va_end(args);
    return false;
}

static bool is_text(const cJSON *value, const char *text)
{
    if (!cJSON_IsString(value)) {
        return false;
    }
    return strcmp(value->valuestring, text) == 0;
}

/** Reads a number that must lie in min..max into *number. Returns false (with err) if it does not. */
static bool read_number(const char *key, const cJSON *value, double min, double max, double *number, char *err,
                        size_t err_len)
{
    bool in_range = cJSON_IsNumber(value) && value->valuedouble >= min && value->valuedouble <= max;
    if (!in_range) {
        return reject(err, err_len, "%s: %.15g..%.15g", key, min, max);
    }
    *number = value->valuedouble;
    return true;
}

/** Numeric settings: each one has its own allowed range from config.h. Any other key is rejected. */
static bool apply_number(app_cfg_t &config, const char *key, const cJSON *value, char *err, size_t err_len)
{
    double number = 0;
    if (strcmp(key, "jpeg_quality") == 0) {
        if (!read_number(key, value, JPEG_QUALITY_MIN, JPEG_QUALITY_MAX, &number, err, err_len)) {
            return false;
        }
        config.jpeg_quality = static_cast<uint8_t>(number);
        return true;
    }
    if (strcmp(key, "xclk_hz") == 0) {
        if (!read_number(key, value, XCLK_HZ_MIN, XCLK_HZ_MAX, &number, err, err_len)) {
            return false;
        }
        config.xclk_hz = static_cast<uint32_t>(number);
        return true;
    }
    if (strcmp(key, "warmup_frames") == 0) {
        if (!read_number(key, value, WARMUP_FRAMES_MIN, WARMUP_FRAMES_MAX, &number, err, err_len)) {
            return false;
        }
        config.warmup_frames = static_cast<uint16_t>(number);
        return true;
    }
    if (strcmp(key, "infer_every_n") == 0) {
        if (!read_number(key, value, INFER_EVERY_N_MIN, INFER_EVERY_N_MAX, &number, err, err_len)) {
            return false;
        }
        config.infer_every_n = static_cast<uint16_t>(number);
        return true;
    }
    if (strcmp(key, "infer_only_runs") == 0) {
        if (!read_number(key, value, INFER_ONLY_RUNS_MIN, INFER_ONLY_RUNS_MAX, &number, err, err_len)) {
            return false;
        }
        config.infer_only_runs = static_cast<uint16_t>(number);
        return true;
    }
    if (strcmp(key, "metrics_interval_ms") == 0) {
        if (!read_number(key, value, METRICS_INTERVAL_MS_MIN, METRICS_INTERVAL_MS_MAX, &number, err, err_len)) {
            return false;
        }
        config.metrics_interval_ms = static_cast<uint32_t>(number);
        return true;
    }
    if (strcmp(key, "grid_every_n") == 0) {
        if (!read_number(key, value, GRID_EVERY_N_MIN, GRID_EVERY_N_MAX, &number, err, err_len)) {
            return false;
        }
        config.grid_every_n = static_cast<uint16_t>(number);
        return true;
    }
    if (strcmp(key, "score_thr") == 0) {
        if (!read_number(key, value, SCORE_THRESHOLD_MIN, SCORE_THRESHOLD_MAX, &number, err, err_len)) {
            return false;
        }
        config.score_thr = static_cast<float>(number);
        return true;
    }
    if (strcmp(key, "thermal_warn_c") == 0) {
        if (!read_number(key, value, THERMAL_WARN_C_MIN, THERMAL_WARN_C_MAX, &number, err, err_len)) {
            return false;
        }
        config.thermal_warn_c = static_cast<float>(number);
        return true;
    }
    return reject(err, err_len, "unknown key '%s'", key);
}

static bool apply_mode(app_cfg_t &config, const cJSON *value, char *err, size_t err_len)
{
    for (size_t index = 0; index < MODE_NAMES.size(); index++) {
        if (is_text(value, MODE_NAMES[index])) {
            config.mode = static_cast<app_mode_t>(index);
            return true;
        }
    }
    return reject(err, err_len, "mode: one of camera_only|inference_only|detect_no_stream|metadata_stream|full_stream");
}

static bool apply_frame_size(app_cfg_t &config, const cJSON *value, char *err, size_t err_len)
{
    for (const frame_size_entry_t &entry : FRAME_SIZES) {
        if (is_text(value, entry.name)) {
            config.frame_size = entry.frame_size;
            return true;
        }
    }
    return reject(err, err_len, "frame_size: one of QQVGA|QVGA|HVGA|VGA");
}

static bool apply_metrics_verbose(app_cfg_t &config, const cJSON *value, char *err, size_t err_len)
{
    if (!cJSON_IsBool(value)) {
        return reject(err, err_len, "metrics_verbose: true|false");
    }
    config.metrics_verbose = cJSON_IsTrue(value);
    return true;
}

static bool apply_wifi_mode(app_cfg_t &config, const cJSON *value, char *err, size_t err_len)
{
    if (is_text(value, "ap")) {
        config.use_soft_ap = true;
        return true;
    }
    if (is_text(value, "sta")) {
        config.use_soft_ap = false;
        return true;
    }
    return reject(err, err_len, "wifi_mode: ap|sta");
}

/** Copies a text setting into a fixed-size field, if it fits. */
static bool apply_text(char *field, size_t field_size, const char *key, const cJSON *value, char *err, size_t err_len)
{
    bool fits = cJSON_IsString(value) && strlen(value->valuestring) < field_size;
    if (!fits) {
        return reject(err, err_len, "%s: string <= %u chars", key, static_cast<unsigned>(field_size - 1));
    }
    strlcpy(field, value->valuestring, field_size);
    return true;
}

/** Applies one "key": value pair. Wi-Fi keys (wifi_mode, sta_ssid, sta_pass) take effect after save + reboot. */
static bool apply_setting(app_cfg_t &config, const cJSON *item, char *err, size_t err_len)
{
    const char *key = item->string;
    if (strcmp(key, "mode") == 0) {
        return apply_mode(config, item, err, err_len);
    }
    if (strcmp(key, "frame_size") == 0) {
        return apply_frame_size(config, item, err, err_len);
    }
    if (strcmp(key, "metrics_verbose") == 0) {
        return apply_metrics_verbose(config, item, err, err_len);
    }
    if (strcmp(key, "wifi_mode") == 0) {
        return apply_wifi_mode(config, item, err, err_len);
    }
    if (strcmp(key, "sta_ssid") == 0) {
        return apply_text(config.sta_ssid, sizeof(config.sta_ssid), key, item, err, err_len);
    }
    if (strcmp(key, "sta_pass") == 0) {
        return apply_text(config.sta_pass, sizeof(config.sta_pass), key, item, err, err_len);
    }
    return apply_number(config, key, item, err, err_len);
}

bool settings_set(const cJSON *values, char *err, size_t err_len)
{
    if (!cJSON_IsObject(values)) {
        return reject(err, err_len, "values must be an object");
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_cfg_t candidate = s_current;
    bool all_valid = true;
    for (const cJSON *item = values->child; item != nullptr; item = item->next) {
        if (!apply_setting(candidate, item, err, err_len)) {
            all_valid = false;
            break;
        }
    }
    if (all_valid) {
        s_current = candidate;
        s_generation++;
    }
    xSemaphoreGive(s_lock);
    return all_valid;
}

bool settings_save()
{
    app_cfg_t config = settings_get();
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) {
        return false;
    }
    bool saved = false;
    if (nvs_set_blob(nvs, NVS_KEY, &config, sizeof(config)) == ESP_OK) {
        if (nvs_commit(nvs) == ESP_OK) {
            saved = true;
        }
    }
    nvs_close(nvs);
    return saved;
}

void settings_to_json(cJSON *obj)
{
    app_cfg_t config = settings_get();
    cJSON_AddStringToObject(obj, "mode", mode_name(config.mode));
    cJSON_AddStringToObject(obj, "frame_size", frame_size_name(config.frame_size));
    cJSON_AddNumberToObject(obj, "jpeg_quality", config.jpeg_quality);
    cJSON_AddNumberToObject(obj, "xclk_hz", config.xclk_hz);
    cJSON_AddNumberToObject(obj, "warmup_frames", config.warmup_frames);
    cJSON_AddNumberToObject(obj, "infer_every_n", config.infer_every_n);
    cJSON_AddNumberToObject(obj, "infer_only_runs", config.infer_only_runs);
    cJSON_AddNumberToObject(obj, "metrics_interval_ms", config.metrics_interval_ms);
    cJSON_AddNumberToObject(obj, "grid_every_n", config.grid_every_n);
    cJSON_AddNumberToObject(obj, "score_thr", config.score_thr);
    cJSON_AddNumberToObject(obj, "thermal_warn_c", config.thermal_warn_c);
    cJSON_AddBoolToObject(obj, "metrics_verbose", config.metrics_verbose);
    cJSON_AddStringToObject(obj, "wifi_mode", wifi_mode_name(config));
    cJSON_AddStringToObject(obj, "sta_ssid", config.sta_ssid);
}
