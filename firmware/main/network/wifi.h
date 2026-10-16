/**
 * @file wifi.h
 * Joining a Wi-Fi network (station), or making the board's own network (SoftAP).
 */
#pragma once
#include <cstdint>
#include "config/settings.h"

void wifi_start(const app_cfg_t &settings);
/** "sta_connecting", "sta_connected" or "ap". */
const char *wifi_state();
const char *wifi_ip();
/** RSSI in dBm while station-connected, otherwise 0. */
int wifi_rssi();
uint32_t wifi_reconnects();
