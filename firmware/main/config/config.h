/**
 * @file config.h
 * All the settings of the board program in one place: a good first file to read.
 *
 * These are the starting values. Most of them can also be changed while the board runs, from
 * the laptop (for example `python pc/main.py check <ip> --set jpeg_quality=20`), without
 * rebuilding; see docs/experiments.md, section 5. They are starting points for your experiments,
 * not recommended values. The *_MIN / *_MAX pairs are the values the "set" command accepts.
 *
 * Units are in the names: _HZ = hertz, _MS = milliseconds, _S = seconds, _C = degrees Celsius,
 * _BYTES = bytes. Unknown words are explained in docs/glossary.md.
 */
#pragma once
#include <cstddef>
#include <cstdint>
#include "esp_camera.h"

/** Experiment modes, switchable at runtime. The names match the laptop's. See docs/experiments.md §4. */
enum class app_mode_t : uint8_t {
    camera_only,
    inference_only,
    detect_no_stream,
    metadata_stream,
    full_stream,
};
constexpr int APP_MODE_COUNT = 5;

constexpr app_mode_t DEFAULT_MODE = app_mode_t::full_stream;

/** Camera clock. If JPEG timeouts or NO-EOI errors appear, 10 MHz is the documented fallback. */
constexpr uint32_t DEFAULT_XCLK_HZ = 20000000;
constexpr uint32_t XCLK_HZ_MIN = 5000000;
constexpr uint32_t XCLK_HZ_MAX = 24000000;
constexpr framesize_t DEFAULT_FRAME_SIZE = FRAMESIZE_QVGA;
/** JPEG quality: lower numbers mean better pictures and bigger files. */
constexpr uint8_t DEFAULT_JPEG_QUALITY = 12;
constexpr uint8_t JPEG_QUALITY_MIN = 4;
constexpr uint8_t JPEG_QUALITY_MAX = 63;
constexpr int CAMERA_FRAME_BUFFER_COUNT = 2;
constexpr uint16_t DEFAULT_WARMUP_FRAMES = 10;
constexpr uint16_t WARMUP_FRAMES_MIN = 0;
constexpr uint16_t WARMUP_FRAMES_MAX = 1000;
constexpr uint16_t EXPECTED_SENSOR_PID = 0x3660;
/** Frames captured by the boot-time camera health check; 0 disables it. */
constexpr int BOOT_CAPTURE_TEST_FRAMES = 1000;

/** Same values as pedestrian_detect::Pico::default_score_thr / default_nms_thr. */
constexpr float DEFAULT_SCORE_THRESHOLD = 0.7f;
constexpr double SCORE_THRESHOLD_MIN = 0.01;
constexpr double SCORE_THRESHOLD_MAX = 1.0;
constexpr float DETECTOR_NMS_THRESHOLD = 0.5f;
constexpr uint16_t DEFAULT_INFER_EVERY_N = 1;
constexpr uint16_t INFER_EVERY_N_MIN = 1;
constexpr uint16_t INFER_EVERY_N_MAX = 1000;
/** inference_only mode runs the embedded still this many times, then idles. */
constexpr uint16_t DEFAULT_INFER_ONLY_RUNS = 100;
constexpr uint16_t INFER_ONLY_RUNS_MIN = 1;
constexpr uint16_t INFER_ONLY_RUNS_MAX = 65535;

constexpr int GRID_COLS = 20;
constexpr int GRID_ROWS = 15;
/** A PRESENCE_GRID record is sent after this many bundles in the streaming modes. */
constexpr uint16_t DEFAULT_GRID_EVERY_N = 50;
constexpr uint16_t GRID_EVERY_N_MIN = 1;
constexpr uint16_t GRID_EVERY_N_MAX = 65535;

constexpr uint32_t DEFAULT_METRICS_INTERVAL_MS = 1000;
constexpr uint32_t METRICS_INTERVAL_MS_MIN = 100;
constexpr uint32_t METRICS_INTERVAL_MS_MAX = 60000;
/** Per stage and per metrics window; extra samples are counted as "overflow", not stored. */
constexpr int METRICS_MAX_SAMPLES_PER_STAGE = 512;
constexpr float DEFAULT_THERMAL_WARN_C = 80.0f;
constexpr float THERMAL_WARN_C_MIN = 30.0f;
constexpr float THERMAL_WARN_C_MAX = 125.0f;
constexpr float THERMAL_HYSTERESIS_C = 3.0f;
/** A queue_drop_burst EVENT is posted when one metrics window drops at least this many bundles. */
constexpr int DROP_BURST_EVENT_THRESHOLD = 10;
constexpr int SERIAL_METRICS_EVERY_S = 10;

constexpr uint16_t TCP_PORT = 5000;
constexpr int SOCKET_SEND_TIMEOUT_S = 5;
/** SoftAP network "GMT-PF-xxxx" at 192.168.4.1, used when no station credentials work. */
constexpr char SOFT_AP_PASSWORD[] = "gmtcamp2026";
constexpr uint8_t SOFT_AP_CHANNEL = 6;
constexpr uint8_t SOFT_AP_MAX_CLIENTS = 2;
constexpr uint32_t WIFI_BACKOFF_MAX_MS = 30000;
constexpr uint32_t WIFI_ATTEMPTS_BEFORE_SOFT_AP = 8;
/** Wi-Fi names are at most 32 characters and passwords at most 64 (plus the closing '\0'). */
constexpr size_t WIFI_SSID_BUFFER_BYTES = 33;
constexpr size_t WIFI_PASSWORD_BUFFER_BYTES = 65;

/**
 * A "bundle" is one finished frame on its way to the laptop: the picture plus its boxes.
 * There are BUNDLE_POOL_SIZE of them, made once at start-up and reused: one being filled by the
 * vision task, one being sent by the net task, and up to READY_QUEUE_DEPTH waiting in between.
 */
constexpr int BUNDLE_POOL_SIZE = 4;
constexpr int READY_QUEUE_DEPTH = 2;
/** A JPEG_FRAME payload (16-byte header + JPEG) must fit the 64 KiB protocol limit. */
constexpr uint32_t JPEG_BUFFER_BYTES = 65536 - 16;
constexpr int MAX_BOXES_PER_FRAME = 16;
/** Metadata modes wait this long for queue space before dropping the oldest bundle. */
constexpr uint32_t METADATA_ENQUEUE_WAIT_MS = 1000;
constexpr int EVENT_QUEUE_DEPTH = 16;
