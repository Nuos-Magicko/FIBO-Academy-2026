/**
 * @file main.cpp
 * Where the board program starts. Boot sequence: board facts, settings, camera capture test,
 * Wi-Fi, then the two tasks that run for the rest of the time:
 *   - the net task (network/net.cpp) on core 0;
 *   - the vision task (vision/vision.cpp) on core 1.
 * Logs go to USB serial only.
 *
 * Folders: config/ (settings), vision/ (camera, AI, presence heatmap), network/ (Wi-Fi, TCP,
 * messages), telemetry/ (measurements, events, temperature), utils/ (small helpers),
 * assets/ (the test photo for the self-test).
 */
#include "config/settings.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "network/net.h"
#include "network/wifi.h"
#include "nvs_flash.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "telemetry/thermal.h"
#include "utils/device.h"
#include "utils/units.h"
#include "vision/bundle.h"
#include "vision/camera.h"
#include "vision/vision.h"

static const char *TAG = "main";
static constexpr uint32_t NET_TASK_STACK_BYTES = 8192;
static constexpr uint32_t VISION_TASK_STACK_BYTES = 16384;
static constexpr UBaseType_t TASK_PRIORITY = 5;
static constexpr BaseType_t NET_CORE = 0;
static constexpr BaseType_t VISION_CORE = 1;

static void log_memory(const char *when)
{
    ESP_LOGI(TAG, "MEM %s: internal free=%u min=%u | psram free=%u min=%u | temp=%.1fC", when,
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
             static_cast<unsigned>(heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM)), thermal_read_c());
}

/** Initializes NVS, erasing it first if its layout is full or from a newer IDF. */
static void init_nvs()
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
}

/** The BOARD lines are read by pc/tools/verify_board.py and pc/tools/soak.py. */
static void log_board_facts()
{
    char revision[CHIP_REVISION_TEXT_BYTES];
    chip_revision_text(revision, sizeof(revision));
    uint32_t flash_bytes = 0;
    esp_flash_get_size(nullptr, &flash_bytes);
    unsigned flash_megabytes = flash_bytes / BYTES_PER_MEGABYTE;
    unsigned psram_megabytes = esp_psram_get_size() / BYTES_PER_MEGABYTE;
    mac_address_t mac = device_mac();
    ESP_LOGI(TAG, "BOARD chip=esp32s3 rev=%s flash=%uMB psram=%uMB mac=%02x:%02x:%02x:%02x:%02x:%02x", revision,
             flash_megabytes, psram_megabytes, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    ESP_LOGI(TAG, "BOARD reset_reason=%s idf=%s", reset_reason_str(), esp_get_idf_version());
    if (esp_reset_reason() == ESP_RST_BROWNOUT) {
        event_post(event_severity_t::error, "brownout_reset", "last reset was a brown-out: check USB power");
    }
}

/** Camera health check on every boot; the vision task then starts the camera again for real use. */
static void run_boot_capture_test(const app_cfg_t &config)
{
    if (BOOT_CAPTURE_TEST_FRAMES <= 0) {
        return;
    }
    if (!camera_start(config)) {
        return;
    }
    camera_capture_test(BOOT_CAPTURE_TEST_FRAMES);
    camera_stop();
}

extern "C" void app_main(void)
{
    camera_install_log_counter();
    cjson_use_psram();
    thermal_init();
    events_init();
    metrics_init();
    init_nvs();
    settings_init();
    log_board_facts();
    log_memory("boot");

    app_cfg_t config = settings_get();
    run_boot_capture_test(config);
    pool_init();
    wifi_start(config);
    log_memory("wifi_started");
    xTaskCreatePinnedToCore(net_task, "net", NET_TASK_STACK_BYTES, nullptr, TASK_PRIORITY, nullptr, NET_CORE);
    xTaskCreatePinnedToCore(vision_task, "vision", VISION_TASK_STACK_BYTES, nullptr, TASK_PRIORITY, nullptr,
                            VISION_CORE);
}
