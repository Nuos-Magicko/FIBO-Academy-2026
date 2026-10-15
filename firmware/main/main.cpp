// GMT people-flow reference app (Gate 2). Boot: board facts, settings, camera capture test,
// Wi-Fi, then net task (core 0) and vision task (core 1). Logs go to USB serial only.
#include "app.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "freertos/task.h"
#include "nvs_flash.h"

static const char *TAG = "main";
counters_t g_cnt;

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

static void *psram_malloc(size_t n) { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM); }
void cjson_use_psram()
{
    cJSON_Hooks h = {psram_malloc, heap_caps_free};
    cJSON_InitHooks(&h);
}

static void log_mem(const char *when)
{
    ESP_LOGI(TAG, "MEM %s: internal free=%u min=%u | psram free=%u min=%u | temp=%.1fC", when,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM), thermal_read_c());
}

extern "C" void app_main(void)
{
    camera_install_log_counter();
    cjson_use_psram();
    thermal_init();
    events_init();
    metrics_init();

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    settings_init();

    esp_chip_info_t chip;
    esp_chip_info(&chip);
    uint32_t flash = 0;
    esp_flash_get_size(NULL, &flash);
    uint8_t mac[6];
    device_mac(mac);
    ESP_LOGI(TAG, "BOARD chip=esp32s3 rev=v%d.%d flash=%uMB psram=%uMB mac=%02x:%02x:%02x:%02x:%02x:%02x",
             chip.revision / 100, chip.revision % 100, (unsigned)(flash >> 20), (unsigned)(esp_psram_get_size() >> 20),
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    ESP_LOGI(TAG, "BOARD reset_reason=%s idf=%s", reset_reason_str(), esp_get_idf_version());
    if (esp_reset_reason() == ESP_RST_BROWNOUT)
        event_post(EV_ERROR, "brownout_reset", "last reset was a brown-out: check USB power");
    log_mem("boot");

    app_cfg_t cfg = settings_get();
    // Boot capture test (Gate 1 evidence on every unit). Camera is then restarted by the vision task.
    if (BOOT_CAPTURE_TEST_N > 0 && camera_start(cfg)) {
        camera_capture_test(BOOT_CAPTURE_TEST_N);
        camera_stop();
    }

    pool_init();
    wifi_start(cfg);
    log_mem("wifi_started");
    xTaskCreatePinnedToCore(net_task, "net", 8192, nullptr, 5, nullptr, 0);
    xTaskCreatePinnedToCore(vision_task, "vision", 16384, nullptr, 5, nullptr, 1);
}
