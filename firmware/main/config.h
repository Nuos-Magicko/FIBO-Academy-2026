// Compile-time defaults. Runtime overrides live in NVS (settings.cpp) and are changed with
// COMMAND {"cmd":"set","values":{...}} + {"cmd":"save"}. These are experiment knobs, not tuning advice.
#pragma once

// Camera (handoff F1). Fallback for JPEG timeouts: DEFAULT_XCLK_HZ 10000000.
#define DEFAULT_XCLK_HZ 20000000
#define DEFAULT_FRAME_SIZE FRAMESIZE_QVGA
#define DEFAULT_JPEG_QUALITY 12
#define CAMERA_FB_COUNT 2
#define DEFAULT_WARMUP_FRAMES 10
#define EXPECTED_SENSOR_PID 0x3660
#define BOOT_CAPTURE_TEST_N 1000 // Gate 1 capture test at every boot; 0 disables

// Detector
#define DEFAULT_SCORE_THR 0.7f // = pedestrian_detect::Pico::default_score_thr
#define NMS_THR 0.5f           // = pedestrian_detect::Pico::default_nms_thr
#define DEFAULT_INFER_EVERY_N 1
#define DEFAULT_INFER_ONLY_RUNS 100 // inference_only: still-image runs before idling

// Modes: camera_only | inference_only | detect_no_stream | metadata_stream | full_stream
#define DEFAULT_MODE MODE_FULL_STREAM

// Presence grid (handoff F3)
#define GRID_COLS 20
#define GRID_ROWS 15
#define DEFAULT_GRID_EVERY_N 50 // send PRESENCE_GRID every N processed frames

// Metrics / events
#define DEFAULT_METRICS_INTERVAL_MS 1000
#define METRICS_MAX_SAMPLES 512 // per stage per window; extra samples counted, not stored
#define DEFAULT_THERMAL_WARN_C 80.0f
#define THERMAL_HYSTERESIS_C 3.0f
#define QUEUE_DROP_BURST 10 // EVENT when a metrics window drops >= this many bundles
#define SERIAL_SUMMARY_EVERY_S 10

// Network
#define TCP_PORT 5000
#define SEND_TIMEOUT_S 5
#define AP_PASS "gmtcamp2026" // SoftAP: SSID "GMT-PF-xxxx", 192.168.4.1
#define AP_CHANNEL 6
#define WIFI_BACKOFF_MAX_MS 30000
#define WIFI_STA_FALLBACK_ATTEMPTS 8 // never connected after this many tries -> SoftAP

// Buffers: 1 filling + 1 sending + READY_DEPTH queued. Fixed at boot.
#define POOL_N 4
#define READY_DEPTH 2
#define JPEG_MAX (65536 - 16) // JPEG_FRAME payload = 16-byte header + JPEG <= 64 KiB
#define MAX_BOXES 16
#define METADATA_ENQUEUE_WAIT_MS 1000 // metadata modes wait this long for queue space before dropping
#define EVENT_QUEUE_DEPTH 16
