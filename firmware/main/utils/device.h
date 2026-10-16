/**
 * @file device.h
 * Small facts about the board and one memory helper, used by several modules.
 */
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

constexpr size_t MAC_ADDRESS_BYTES = 6;
using mac_address_t = std::array<uint8_t, MAC_ADDRESS_BYTES>;

/** "v0.2"-style chip revision text needs at most this many bytes. */
constexpr size_t CHIP_REVISION_TEXT_BYTES = 8;

/** Why the board last restarted, as text: "POWERON", "USB", "BROWNOUT", ... */
const char *reset_reason_str();
/** The Wi-Fi station MAC address, which identifies the board. */
mac_address_t device_mac();
/** Writes the chip revision as "v<major>.<minor>" into text. */
void chip_revision_text(char *text, size_t size);
/** Makes cJSON allocate from PSRAM so JSON records never fragment internal RAM. */
void cjson_use_psram();
