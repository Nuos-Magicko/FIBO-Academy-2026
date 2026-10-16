/**
 * @file device.cpp
 * Reset reason, MAC address, chip revision and the cJSON memory hook.
 */
#include <cstdio>
#include "utils/device.h"
#include "cJSON.h"
#include "esp_chip_info.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "esp_system.h"

/** esp_chip_info() reports the revision as major * 100 + minor. */
static constexpr int CHIP_REVISION_MAJOR_FACTOR = 100;

const char *reset_reason_str()
{
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON:
        return "POWERON";
    case ESP_RST_EXT:
        return "EXT";
    case ESP_RST_SW:
        return "SW";
    case ESP_RST_PANIC:
        return "PANIC";
    case ESP_RST_INT_WDT:
        return "INT_WDT";
    case ESP_RST_TASK_WDT:
        return "TASK_WDT";
    case ESP_RST_WDT:
        return "WDT";
    case ESP_RST_DEEPSLEEP:
        return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:
        return "BROWNOUT";
    case ESP_RST_SDIO:
        return "SDIO";
    case ESP_RST_USB:
        return "USB";
    case ESP_RST_JTAG:
        return "JTAG";
    default:
        return "UNKNOWN";
    }
}

mac_address_t device_mac()
{
    mac_address_t mac = {};
    esp_read_mac(mac.data(), ESP_MAC_WIFI_STA);
    return mac;
}

void chip_revision_text(char *text, size_t size)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    int major = chip.revision / CHIP_REVISION_MAJOR_FACTOR;
    int minor = chip.revision % CHIP_REVISION_MAJOR_FACTOR;
    snprintf(text, size, "v%d.%d", major, minor);
}

static void *psram_malloc(size_t size)
{
    return heap_caps_malloc(size, MALLOC_CAP_SPIRAM);
}

void cjson_use_psram()
{
    cJSON_Hooks hooks;
    hooks.malloc_fn = psram_malloc;
    hooks.free_fn = heap_caps_free;
    cJSON_InitHooks(&hooks);
}
