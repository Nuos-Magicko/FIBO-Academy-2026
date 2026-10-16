/**
 * @file device.h
 * Small facts about the board and one memory helper, used by several modules.
 */
#pragma once
#include <cstdint>

/** Why the board last restarted, as text: "POWERON", "USB", "BROWNOUT", ... */
const char *reset_reason_str();
void device_mac(uint8_t mac[6]);
/** Makes cJSON allocate from PSRAM so JSON records never fragment internal RAM. */
void cjson_use_psram();
