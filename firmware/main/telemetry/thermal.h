/**
 * @file thermal.h
 * The chip's own temperature sensor.
 */
#pragma once

void thermal_init();
float thermal_read_c();
/** Posts thermal_warning above warn_c and thermal_ok once below warn_c - THERMAL_HYSTERESIS_C. */
void thermal_check(float warn_c);
