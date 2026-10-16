/**
 * @file device.cpp
 * Reset reason, MAC address and the cJSON memory hook.
 */
#include "utils/device.h"
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "esp_system.h"

const char *reset_reason_str()
{
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_EXT: return "EXT";
    case ESP_RST_SW: return "SW";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SDIO: return "SDIO";
    case ESP_RST_USB: return "USB";
    case ESP_RST_JTAG: return "JTAG";
    default: return "UNKNOWN";
    }
}

void device_mac(uint8_t mac[6]) { esp_read_mac(mac, ESP_MAC_WIFI_STA); }

static void *psram_malloc(size_t size) { return heap_caps_malloc(size, MALLOC_CAP_SPIRAM); }

void cjson_use_psram()
{
    cJSON_Hooks hooks = {psram_malloc, heap_caps_free};
    cJSON_InitHooks(&hooks);
}
