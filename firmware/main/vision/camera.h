/**
 * @file camera.h
 * The OV3660 camera on the XIAO ESP32S3 Sense.
 */
#pragma once
#include <cstdint>
#include "config/settings.h"

/** Initializes the camera, checks the sensor PID (posts sensor_mismatch) and discards warm-up frames. */
bool camera_start(const app_cfg_t &settings);
void camera_stop();
/** Captures frame_count frames and posts a capture_test EVENT with success, failure and size counts. */
void camera_capture_test(int frame_count);
/** Counts the NO-EOI / NO-SOI / FB-OVF errors that esp32-camera reports only as log lines. */
void camera_install_log_counter();
uint16_t camera_sensor_pid();
