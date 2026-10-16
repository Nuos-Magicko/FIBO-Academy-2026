/**
 * @file settings.h
 * The settings the board runs with right now: the defaults from config.h, possibly changed from
 * the laptop with the "set" command and saved in flash (NVS) with "save".
 */
#pragma once
#include <cstddef>
#include <cstdint>
#include "cJSON.h"
#include "config/config.h"

const char *mode_name(app_mode_t mode);
const char *frame_size_name(framesize_t frame_size);

/** Runtime configuration. Field names match the keys of the "set" command and of HELLO.config. */
struct app_cfg_t {
    /** Layout version; an NVS blob with a different value is ignored. */
    uint32_t version;
    app_mode_t mode;
    framesize_t frame_size;
    uint8_t jpeg_quality;
    uint32_t xclk_hz;
    uint16_t warmup_frames;
    uint16_t infer_every_n;
    uint16_t infer_only_runs;
    uint32_t metrics_interval_ms;
    uint16_t grid_every_n;
    float score_thr;
    float thermal_warn_c;
    bool metrics_verbose;
    bool use_soft_ap;
    char sta_ssid[WIFI_SSID_BUFFER_BYTES];
    char sta_pass[WIFI_PASSWORD_BUFFER_BYTES];
};

/** Loads compile-time defaults, then any saved NVS overrides. */
void settings_init();
app_cfg_t settings_get();
/** Increases on every successful settings_set(), so tasks can notice changes cheaply. */
uint32_t settings_generation();
/**
 * Applies {"key": value, ...}. Every key is validated before anything changes, so an invalid
 * request changes nothing and err explains why.
 */
bool settings_set(const cJSON *values, char *err, size_t err_len);
bool settings_save();
/** Adds every setting to obj except the Wi-Fi password. */
void settings_to_json(cJSON *obj);
