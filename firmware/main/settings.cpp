// Runtime configuration: compile-time defaults (config.h, wifi_secrets.h) overridden by an NVS blob.
#include <cstring>
#include "app.h"
#include "esp_log.h"
#include "freertos/semphr.h"
#include "nvs.h"

// wifi_secrets.h is git-ignored (WIFI_STA_SSID / WIFI_STA_PASS). Creating or deleting it is not seen
// by an incremental build: touch settings.cpp or run `idf.py fullclean` afterwards.
#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#endif

static const char *TAG = "settings";
#define SETTINGS_VERSION 1
#define NVS_NS "gmt"
#define NVS_KEY "cfg"

static app_cfg_t s_cfg;
static uint32_t s_gen;
static SemaphoreHandle_t s_mtx;

static const char *const MODE_NAMES[MODE_COUNT] = {"camera_only", "inference_only", "detect_no_stream",
                                                   "metadata_stream", "full_stream"};
const char *mode_name(app_mode_t m) { return m < MODE_COUNT ? MODE_NAMES[m] : "?"; }

static const struct {
    framesize_t f;
    const char *name;
} FRAME_SIZES[] = {{FRAMESIZE_QQVGA, "QQVGA"}, {FRAMESIZE_QVGA, "QVGA"}, {FRAMESIZE_HVGA, "HVGA"}, {FRAMESIZE_VGA, "VGA"}};

const char *frame_size_name(framesize_t f)
{
    for (auto &e : FRAME_SIZES)
        if (e.f == f) return e.name;
    return "?";
}

static app_cfg_t defaults()
{
    app_cfg_t c = {};
    c.version = SETTINGS_VERSION;
    c.mode = DEFAULT_MODE;
    c.frame_size = DEFAULT_FRAME_SIZE;
    c.jpeg_quality = DEFAULT_JPEG_QUALITY;
    c.xclk_hz = DEFAULT_XCLK_HZ;
    c.warmup_frames = DEFAULT_WARMUP_FRAMES;
    c.infer_every_n = DEFAULT_INFER_EVERY_N;
    c.infer_only_runs = DEFAULT_INFER_ONLY_RUNS;
    c.metrics_interval_ms = DEFAULT_METRICS_INTERVAL_MS;
    c.grid_every_n = DEFAULT_GRID_EVERY_N;
    c.score_thr = DEFAULT_SCORE_THR;
    c.thermal_warn_c = DEFAULT_THERMAL_WARN_C;
#ifdef WIFI_STA_SSID
    c.wifi_ap = false;
    strlcpy(c.sta_ssid, WIFI_STA_SSID, sizeof c.sta_ssid);
    strlcpy(c.sta_pass, WIFI_STA_PASS, sizeof c.sta_pass);
#else
    c.wifi_ap = true;
#endif
    return c;
}

void settings_init()
{
    s_mtx = xSemaphoreCreateMutex();
    s_cfg = defaults();
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        app_cfg_t c;
        size_t n = sizeof c;
        if (nvs_get_blob(h, NVS_KEY, &c, &n) == ESP_OK && n == sizeof c && c.version == SETTINGS_VERSION) {
            s_cfg = c;
            ESP_LOGI(TAG, "loaded overrides from NVS");
        }
        nvs_close(h);
    }
    ESP_LOGI(TAG, "mode=%s frame=%s q=%u xclk=%u wifi=%s", mode_name(s_cfg.mode), frame_size_name(s_cfg.frame_size),
             s_cfg.jpeg_quality, (unsigned)s_cfg.xclk_hz, s_cfg.wifi_ap ? "ap" : "sta");
}

app_cfg_t settings_get()
{
    xSemaphoreTake(s_mtx, portMAX_DELAY);
    app_cfg_t c = s_cfg;
    xSemaphoreGive(s_mtx);
    return c;
}

uint32_t settings_generation() { return __atomic_load_n(&s_gen, __ATOMIC_ACQUIRE); }

static bool num_in(const cJSON *v, double lo, double hi, double *out)
{
    if (!cJSON_IsNumber(v) || v->valuedouble < lo || v->valuedouble > hi) return false;
    *out = v->valuedouble;
    return true;
}

// Returns false with err set if key/value is invalid. Writes into c.
static bool apply_one(app_cfg_t &c, const cJSON *v, char *err, size_t errlen)
{
    const char *k = v->string;
    double d;
#define BAD(...) (snprintf(err, errlen, __VA_ARGS__), false)
    if (!strcmp(k, "mode")) {
        for (int i = 0; i < MODE_COUNT; i++)
            if (cJSON_IsString(v) && !strcmp(v->valuestring, MODE_NAMES[i])) return c.mode = (app_mode_t)i, true;
        return BAD("mode: one of camera_only|inference_only|detect_no_stream|metadata_stream|full_stream");
    }
    if (!strcmp(k, "frame_size")) {
        for (auto &e : FRAME_SIZES)
            if (cJSON_IsString(v) && !strcmp(v->valuestring, e.name)) return c.frame_size = e.f, true;
        return BAD("frame_size: one of QQVGA|QVGA|HVGA|VGA");
    }
    if (!strcmp(k, "jpeg_quality")) return num_in(v, 4, 63, &d) ? (c.jpeg_quality = d, true) : BAD("jpeg_quality: 4..63");
    if (!strcmp(k, "xclk_hz"))
        return num_in(v, 5e6, 24e6, &d) ? (c.xclk_hz = d, true) : BAD("xclk_hz: 5000000..24000000");
    if (!strcmp(k, "warmup_frames")) return num_in(v, 0, 1000, &d) ? (c.warmup_frames = d, true) : BAD("warmup_frames: 0..1000");
    if (!strcmp(k, "infer_every_n")) return num_in(v, 1, 1000, &d) ? (c.infer_every_n = d, true) : BAD("infer_every_n: 1..1000");
    if (!strcmp(k, "infer_only_runs"))
        return num_in(v, 1, 65535, &d) ? (c.infer_only_runs = d, true) : BAD("infer_only_runs: 1..65535");
    if (!strcmp(k, "metrics_interval_ms"))
        return num_in(v, 100, 60000, &d) ? (c.metrics_interval_ms = d, true) : BAD("metrics_interval_ms: 100..60000");
    if (!strcmp(k, "grid_every_n")) return num_in(v, 1, 65535, &d) ? (c.grid_every_n = d, true) : BAD("grid_every_n: 1..65535");
    if (!strcmp(k, "score_thr")) return num_in(v, 0.01, 1.0, &d) ? (c.score_thr = d, true) : BAD("score_thr: 0.01..1.0");
    if (!strcmp(k, "thermal_warn_c"))
        return num_in(v, 30, 125, &d) ? (c.thermal_warn_c = d, true) : BAD("thermal_warn_c: 30..125");
    if (!strcmp(k, "metrics_verbose"))
        return cJSON_IsBool(v) ? (c.metrics_verbose = cJSON_IsTrue(v), true) : BAD("metrics_verbose: true|false");
    // Wi-Fi keys take effect after save + reboot
    if (!strcmp(k, "wifi_mode")) {
        if (cJSON_IsString(v) && !strcmp(v->valuestring, "ap")) return c.wifi_ap = true, true;
        if (cJSON_IsString(v) && !strcmp(v->valuestring, "sta")) return c.wifi_ap = false, true;
        return BAD("wifi_mode: ap|sta");
    }
    if (!strcmp(k, "sta_ssid"))
        return cJSON_IsString(v) && strlen(v->valuestring) < sizeof c.sta_ssid ? (strlcpy(c.sta_ssid, v->valuestring, sizeof c.sta_ssid), true)
                                                                               : BAD("sta_ssid: string <= 32 chars");
    if (!strcmp(k, "sta_pass"))
        return cJSON_IsString(v) && strlen(v->valuestring) < sizeof c.sta_pass ? (strlcpy(c.sta_pass, v->valuestring, sizeof c.sta_pass), true)
                                                                               : BAD("sta_pass: string <= 64 chars");
    return BAD("unknown key '%s'", k);
#undef BAD
}

bool settings_set(const cJSON *values, char *err, size_t errlen)
{
    if (!cJSON_IsObject(values)) return snprintf(err, errlen, "values must be an object"), false;
    xSemaphoreTake(s_mtx, portMAX_DELAY);
    app_cfg_t c = s_cfg;
    bool ok = true;
    for (const cJSON *v = values->child; v && ok; v = v->next) ok = apply_one(c, v, err, errlen);
    if (ok) {
        s_cfg = c;
        __atomic_add_fetch(&s_gen, 1, __ATOMIC_RELEASE);
    }
    xSemaphoreGive(s_mtx);
    return ok;
}

bool settings_save()
{
    app_cfg_t c = settings_get();
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) return false;
    bool ok = nvs_set_blob(h, NVS_KEY, &c, sizeof c) == ESP_OK && nvs_commit(h) == ESP_OK;
    nvs_close(h);
    return ok;
}

void settings_to_json(cJSON *o)
{
    app_cfg_t c = settings_get();
    cJSON_AddStringToObject(o, "mode", mode_name(c.mode));
    cJSON_AddStringToObject(o, "frame_size", frame_size_name(c.frame_size));
    cJSON_AddNumberToObject(o, "jpeg_quality", c.jpeg_quality);
    cJSON_AddNumberToObject(o, "xclk_hz", c.xclk_hz);
    cJSON_AddNumberToObject(o, "warmup_frames", c.warmup_frames);
    cJSON_AddNumberToObject(o, "infer_every_n", c.infer_every_n);
    cJSON_AddNumberToObject(o, "infer_only_runs", c.infer_only_runs);
    cJSON_AddNumberToObject(o, "metrics_interval_ms", c.metrics_interval_ms);
    cJSON_AddNumberToObject(o, "grid_every_n", c.grid_every_n);
    cJSON_AddNumberToObject(o, "score_thr", c.score_thr);
    cJSON_AddNumberToObject(o, "thermal_warn_c", c.thermal_warn_c);
    cJSON_AddBoolToObject(o, "metrics_verbose", c.metrics_verbose);
    cJSON_AddStringToObject(o, "wifi_mode", c.wifi_ap ? "ap" : "sta");
    cJSON_AddStringToObject(o, "sta_ssid", c.sta_ssid);
}
