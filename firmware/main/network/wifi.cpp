/**
 * @file wifi.cpp
 * Wi-Fi station with capped exponential backoff, or SoftAP when configured. If the station never
 * connects within WIFI_ATTEMPTS_BEFORE_SOFT_AP tries after boot, the board falls back to SoftAP.
 * Power save stays at the IDF default (MIN_MODEM in station mode); it is recorded, not tuned.
 */
#include <cstring>
#include "network/wifi.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"

static const char *TAG = "wifi";
static constexpr char STATE_STATION_CONNECTING[] = "sta_connecting";
static constexpr char STATE_STATION_CONNECTED[] = "sta_connected";
static constexpr char STATE_SOFT_AP[] = "ap";
static constexpr char SOFT_AP_IP[] = "192.168.4.1";
static constexpr char NO_IP[] = "0.0.0.0";
static constexpr uint32_t FIRST_RETRY_DELAY_MS = 1000;
static constexpr uint32_t BACKOFF_MAX_DOUBLINGS = 5;
static constexpr uint32_t LOG_EVERY_NTH_FAILED_ATTEMPT = 10;

static constexpr char STATE_OFF[] = "off";
static const char *s_state = STATE_OFF;
static char s_ip[16] = "0.0.0.0";
static uint32_t s_failed_attempts, s_reconnects;
static bool s_ever_connected;
static esp_timer_handle_t s_retry_timer;

const char *wifi_state() { return s_state; }
const char *wifi_ip() { return s_ip; }
uint32_t wifi_reconnects() { return s_reconnects; }

int wifi_rssi()
{
    wifi_ap_record_t access_point;
    bool connected = s_state == STATE_STATION_CONNECTED;
    return connected && esp_wifi_sta_get_ap_info(&access_point) == ESP_OK ? access_point.rssi : 0;
}

/** SoftAP "GMT-PF-xxxx" (last two MAC bytes), WPA2, at SOFT_AP_IP. */
static void start_soft_ap()
{
    wifi_config_t config = {};
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    config.ap.ssid_len = snprintf((char *)config.ap.ssid, sizeof config.ap.ssid, "GMT-PF-%02X%02X", mac[4], mac[5]);
    strlcpy((char *)config.ap.password, SOFT_AP_PASSWORD, sizeof config.ap.password);
    config.ap.channel = SOFT_AP_CHANNEL;
    config.ap.max_connection = 2;
    config.ap.authmode = WIFI_AUTH_WPA2_PSK;
    esp_wifi_set_mode(WIFI_MODE_AP);
    esp_wifi_set_config(WIFI_IF_AP, &config);
    strcpy(s_ip, SOFT_AP_IP);
    s_state = STATE_SOFT_AP;
    ESP_LOGI(TAG, "NET mode=ap ssid=%s ip=%s port=%d", (char *)config.ap.ssid, s_ip, TCP_PORT);
}

static void configure_station(const app_cfg_t &cfg)
{
    wifi_config_t config = {};
    strlcpy((char *)config.sta.ssid, cfg.sta_ssid, sizeof config.sta.ssid);
    strlcpy((char *)config.sta.password, cfg.sta_pass, sizeof config.sta.password);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    s_state = STATE_STATION_CONNECTING;
    ESP_LOGI(TAG, "NET mode=sta ssid=%s connecting", cfg.sta_ssid);
}

/** 1 s, 2 s, 4 s, ... capped at WIFI_BACKOFF_MAX_MS. */
static uint32_t backoff_delay_ms(uint32_t failed_attempts)
{
    uint32_t doublings = failed_attempts > BACKOFF_MAX_DOUBLINGS ? BACKOFF_MAX_DOUBLINGS : failed_attempts - 1;
    uint32_t delay = FIRST_RETRY_DELAY_MS << doublings;
    return delay > WIFI_BACKOFF_MAX_MS ? WIFI_BACKOFF_MAX_MS : delay;
}

/** Runs in the esp_timer task after each backoff delay. */
static void retry_or_fall_back(void *)
{
    if (!s_ever_connected && s_failed_attempts >= WIFI_ATTEMPTS_BEFORE_SOFT_AP) {
        event_post(EV_WARN, "wifi_fallback_ap", "station never connected after %u tries", (unsigned)s_failed_attempts);
        start_soft_ap();
        return;
    }
    esp_wifi_connect();
}

static void on_station_disconnected(const wifi_event_sta_disconnected_t *info)
{
    if (s_state == STATE_SOFT_AP) return;
    bool was_connected = s_state == STATE_STATION_CONNECTED;
    s_state = STATE_STATION_CONNECTING;
    strcpy(s_ip, NO_IP);
    g_counters.wifi_disconnects++;
    s_failed_attempts++;
    uint32_t delay_ms = backoff_delay_ms(s_failed_attempts);
    if (was_connected || s_failed_attempts == 1 || s_failed_attempts % LOG_EVERY_NTH_FAILED_ATTEMPT == 0)
        event_post(EV_WARN, "wifi_disconnected", "reason=%d attempt=%u retry_in=%ums", info->reason,
                   (unsigned)s_failed_attempts, (unsigned)delay_ms);
    esp_timer_start_once(s_retry_timer, (uint64_t)delay_ms * 1000);
}

static void on_got_ip(const ip_event_got_ip_t *info)
{
    snprintf(s_ip, sizeof s_ip, IPSTR, IP2STR(&info->ip_info.ip));
    if (s_ever_connected) s_reconnects++;
    s_ever_connected = true;
    s_failed_attempts = 0;
    s_state = STATE_STATION_CONNECTED;
    ESP_LOGI(TAG, "NET mode=sta ip=%s port=%d", s_ip, TCP_PORT);
    event_post(EV_INFO, "wifi_connected", "ip=%s reconnects=%u", s_ip, (unsigned)s_reconnects);
}

static void on_wifi_event(void *, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED)
        on_station_disconnected((wifi_event_sta_disconnected_t *)data);
    else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) on_got_ip((ip_event_got_ip_t *)data);
}

void wifi_start(const app_cfg_t &cfg)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    esp_netif_create_default_wifi_ap();
    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_config));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi_event, nullptr));
    esp_timer_create_args_t timer_args = {};
    timer_args.callback = retry_or_fall_back;
    timer_args.name = "wifi_retry";
    ESP_ERROR_CHECK(esp_timer_create(&timer_args, &s_retry_timer));

    if (cfg.use_soft_ap || !cfg.sta_ssid[0]) start_soft_ap();
    else configure_station(cfg);
    ESP_ERROR_CHECK(esp_wifi_start());
}
