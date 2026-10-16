/**
 * @file settings.cpp
 * Runtime configuration: compile-time defaults (config.h, wifi_secrets.h) overridden by an NVS blob.
 *
 * wifi_secrets.h is git-ignored and defines WIFI_STA_SSID / WIFI_STA_PASS. An incremental build does
 * not notice the file appearing or disappearing: touch this file or run `idf.py fullclean` after.
 */
#include <cstdarg>
#include <cstring>
#include "config/settings.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#endif

static const char *TAG = "settings";
static constexpr uint32_t SETTINGS_LAYOUT_VERSION = 1;
static constexpr char NVS_NAMESPACE[] = "gmt";
static constexpr char NVS_KEY[] = "cfg";

static app_cfg_t s_current;
static uint32_t s_generation;
static SemaphoreHandle_t s_lock;

static const char *const MODE_NAMES[MODE_COUNT] = {"camera_only", "inference_only", "detect_no_stream",
                                                   "metadata_stream", "full_stream"};

static const struct {
    framesize_t frame_size;
    const char *name;
} FRAME_SIZES[] = {{FRAMESIZE_QQVGA, "QQVGA"}, {FRAMESIZE_QVGA, "QVGA"}, {FRAMESIZE_HVGA, "HVGA"}, {FRAMESIZE_VGA, "VGA"}};

/** A numeric setting accepted by the "set" command, with its inclusive range. */
struct numeric_setting_t {
    const char *key;
    double min, max;
    void (*assign)(app_cfg_t &cfg, double value);
};

static const numeric_setting_t NUMERIC_SETTINGS[] = {
    {"jpeg_quality", 4, 63, [](app_cfg_t &c, double v) { c.jpeg_quality = v; }},
    {"xclk_hz", 5e6, 24e6, [](app_cfg_t &c, double v) { c.xclk_hz = v; }},
    {"warmup_frames", 0, 1000, [](app_cfg_t &c, double v) { c.warmup_frames = v; }},
    {"infer_every_n", 1, 1000, [](app_cfg_t &c, double v) { c.infer_every_n = v; }},
    {"infer_only_runs", 1, 65535, [](app_cfg_t &c, double v) { c.infer_only_runs = v; }},
    {"metrics_interval_ms", 100, 60000, [](app_cfg_t &c, double v) { c.metrics_interval_ms = v; }},
    {"grid_every_n", 1, 65535, [](app_cfg_t &c, double v) { c.grid_every_n = v; }},
    {"score_thr", 0.01, 1.0, [](app_cfg_t &c, double v) { c.score_thr = v; }},
    {"thermal_warn_c", 30, 125, [](app_cfg_t &c, double v) { c.thermal_warn_c = v; }},
};

const char *mode_name(app_mode_t mode) { return mode < MODE_COUNT ? MODE_NAMES[mode] : "?"; }

const char *frame_size_name(framesize_t frame_size)
{
    for (auto &entry : FRAME_SIZES)
        if (entry.frame_size == frame_size) return entry.name;
    return "?";
}

static app_cfg_t compile_time_defaults()
{
    app_cfg_t c = {};
    c.version = SETTINGS_LAYOUT_VERSION;
    c.mode = DEFAULT_MODE;
    c.frame_size = DEFAULT_FRAME_SIZE;
    c.jpeg_quality = DEFAULT_JPEG_QUALITY;
    c.xclk_hz = DEFAULT_XCLK_HZ;
    c.warmup_frames = DEFAULT_WARMUP_FRAMES;
    c.infer_every_n = DEFAULT_INFER_EVERY_N;
    c.infer_only_runs = DEFAULT_INFER_ONLY_RUNS;
    c.metrics_interval_ms = DEFAULT_METRICS_INTERVAL_MS;
    c.grid_every_n = DEFAULT_GRID_EVERY_N;
    c.score_thr = DEFAULT_SCORE_THRESHOLD;
    c.thermal_warn_c = DEFAULT_THERMAL_WARN_C;
#ifdef WIFI_STA_SSID
    c.use_soft_ap = false;
    strlcpy(c.sta_ssid, WIFI_STA_SSID, sizeof c.sta_ssid);
    strlcpy(c.sta_pass, WIFI_STA_PASS, sizeof c.sta_pass);
#else
    c.use_soft_ap = true;
#endif
    return c;
}

static bool load_saved_settings(app_cfg_t *out)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) return false;
    app_cfg_t saved;
    size_t size = sizeof saved;
    bool valid = nvs_get_blob(nvs, NVS_KEY, &saved, &size) == ESP_OK && size == sizeof saved &&
                 saved.version == SETTINGS_LAYOUT_VERSION;
    nvs_close(nvs);
    if (valid) *out = saved;
    return valid;
}

void settings_init()
{
    s_lock = xSemaphoreCreateMutex();
    s_current = compile_time_defaults();
    if (load_saved_settings(&s_current)) ESP_LOGI(TAG, "loaded overrides from NVS");
    ESP_LOGI(TAG, "mode=%s frame=%s q=%u xclk=%u wifi=%s", mode_name(s_current.mode),
             frame_size_name(s_current.frame_size), s_current.jpeg_quality, (unsigned)s_current.xclk_hz,
             s_current.use_soft_ap ? "ap" : "sta");
}

app_cfg_t settings_get()
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_cfg_t copy = s_current;
    xSemaphoreGive(s_lock);
    return copy;
}

uint32_t settings_generation() { return __atomic_load_n(&s_generation, __ATOMIC_ACQUIRE); }

static bool reject(char *err, size_t err_len, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static bool reject(char *err, size_t err_len, const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    vsnprintf(err, err_len, fmt, args);
    va_end(args);
    return false;
}

static bool apply_numeric(app_cfg_t &cfg, const numeric_setting_t &setting, const cJSON *value, char *err,
                          size_t err_len)
{
    if (!cJSON_IsNumber(value) || value->valuedouble < setting.min || value->valuedouble > setting.max)
        return reject(err, err_len, "%s: %.15g..%.15g", setting.key, setting.min, setting.max);
    setting.assign(cfg, value->valuedouble);
    return true;
}

static bool apply_mode(app_cfg_t &cfg, const cJSON *value, char *err, size_t err_len)
{
    for (int i = 0; i < MODE_COUNT; i++) {
        if (cJSON_IsString(value) && !strcmp(value->valuestring, MODE_NAMES[i])) {
            cfg.mode = (app_mode_t)i;
            return true;
        }
    }
    return reject(err, err_len, "mode: one of camera_only|inference_only|detect_no_stream|metadata_stream|full_stream");
}

static bool apply_frame_size(app_cfg_t &cfg, const cJSON *value, char *err, size_t err_len)
{
    for (auto &entry : FRAME_SIZES) {
        if (cJSON_IsString(value) && !strcmp(value->valuestring, entry.name)) {
            cfg.frame_size = entry.frame_size;
            return true;
        }
    }
    return reject(err, err_len, "frame_size: one of QQVGA|QVGA|HVGA|VGA");
}

static bool apply_wifi_mode(app_cfg_t &cfg, const cJSON *value, char *err, size_t err_len)
{
    const bool is_string = cJSON_IsString(value);
    if (is_string && !strcmp(value->valuestring, "ap")) {
        cfg.use_soft_ap = true;
        return true;
    }
    if (is_string && !strcmp(value->valuestring, "sta")) {
        cfg.use_soft_ap = false;
        return true;
    }
    return reject(err, err_len, "wifi_mode: ap|sta");
}

static bool apply_string(char *field, size_t field_size, const char *key, const cJSON *value, char *err,
                         size_t err_len)
{
    if (!cJSON_IsString(value) || strlen(value->valuestring) >= field_size)
        return reject(err, err_len, "%s: string <= %u chars", key, (unsigned)(field_size - 1));
    strlcpy(field, value->valuestring, field_size);
    return true;
}

/** Wi-Fi keys (wifi_mode, sta_ssid, sta_pass) take effect only after save + reboot. */
static bool apply_setting(app_cfg_t &cfg, const cJSON *item, char *err, size_t err_len)
{
    const char *key = item->string;
    for (auto &setting : NUMERIC_SETTINGS)
        if (!strcmp(key, setting.key)) return apply_numeric(cfg, setting, item, err, err_len);
    if (!strcmp(key, "mode")) return apply_mode(cfg, item, err, err_len);
    if (!strcmp(key, "frame_size")) return apply_frame_size(cfg, item, err, err_len);
    if (!strcmp(key, "metrics_verbose")) {
        if (!cJSON_IsBool(item)) return reject(err, err_len, "metrics_verbose: true|false");
        cfg.metrics_verbose = cJSON_IsTrue(item);
        return true;
    }
    if (!strcmp(key, "wifi_mode")) return apply_wifi_mode(cfg, item, err, err_len);
    if (!strcmp(key, "sta_ssid")) return apply_string(cfg.sta_ssid, sizeof cfg.sta_ssid, key, item, err, err_len);
    if (!strcmp(key, "sta_pass")) return apply_string(cfg.sta_pass, sizeof cfg.sta_pass, key, item, err, err_len);
    return reject(err, err_len, "unknown key '%s'", key);
}

bool settings_set(const cJSON *values, char *err, size_t err_len)
{
    if (!cJSON_IsObject(values)) return reject(err, err_len, "values must be an object");
    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_cfg_t candidate = s_current;
    bool all_valid = true;
    for (const cJSON *item = values->child; item && all_valid; item = item->next)
        all_valid = apply_setting(candidate, item, err, err_len);
    if (all_valid) {
        s_current = candidate;
        __atomic_add_fetch(&s_generation, 1, __ATOMIC_RELEASE);
    }
    xSemaphoreGive(s_lock);
    return all_valid;
}

bool settings_save()
{
    app_cfg_t cfg = settings_get();
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) return false;
    bool saved = nvs_set_blob(nvs, NVS_KEY, &cfg, sizeof cfg) == ESP_OK && nvs_commit(nvs) == ESP_OK;
    nvs_close(nvs);
    return saved;
}

void settings_to_json(cJSON *obj)
{
    app_cfg_t c = settings_get();
    cJSON_AddStringToObject(obj, "mode", mode_name(c.mode));
    cJSON_AddStringToObject(obj, "frame_size", frame_size_name(c.frame_size));
    cJSON_AddNumberToObject(obj, "jpeg_quality", c.jpeg_quality);
    cJSON_AddNumberToObject(obj, "xclk_hz", c.xclk_hz);
    cJSON_AddNumberToObject(obj, "warmup_frames", c.warmup_frames);
    cJSON_AddNumberToObject(obj, "infer_every_n", c.infer_every_n);
    cJSON_AddNumberToObject(obj, "infer_only_runs", c.infer_only_runs);
    cJSON_AddNumberToObject(obj, "metrics_interval_ms", c.metrics_interval_ms);
    cJSON_AddNumberToObject(obj, "grid_every_n", c.grid_every_n);
    cJSON_AddNumberToObject(obj, "score_thr", c.score_thr);
    cJSON_AddNumberToObject(obj, "thermal_warn_c", c.thermal_warn_c);
    cJSON_AddBoolToObject(obj, "metrics_verbose", c.metrics_verbose);
    cJSON_AddStringToObject(obj, "wifi_mode", c.use_soft_ap ? "ap" : "sta");
    cJSON_AddStringToObject(obj, "sta_ssid", c.sta_ssid);
}
