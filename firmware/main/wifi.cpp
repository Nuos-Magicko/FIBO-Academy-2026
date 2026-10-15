// Wi-Fi: station with capped exponential backoff; SoftAP if configured, or if the station never
// connects within WIFI_STA_FALLBACK_ATTEMPTS tries after boot. State is reported in METRICS.
#include <cstring>
#include "app.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"

static const char *TAG = "wifi";
static const char *s_state = "off";
static char s_ip[16] = "0.0.0.0";
static uint32_t s_attempts, s_reconnects;
static bool s_ever_connected;
static esp_timer_handle_t s_retry_timer;

const char *wifi_state() { return s_state; }
const char *wifi_ip() { return s_ip; }
uint32_t wifi_reconnects() { return s_reconnects; }
int wifi_rssi()
{
    wifi_ap_record_t ap;
    return strcmp(s_state, "sta_connected") == 0 && esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}

static void start_ap()
{
    wifi_config_t wc = {};
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    wc.ap.ssid_len = snprintf((char *)wc.ap.ssid, sizeof wc.ap.ssid, "GMT-PF-%02X%02X", mac[4], mac[5]);
    strlcpy((char *)wc.ap.password, AP_PASS, sizeof wc.ap.password);
    wc.ap.channel = AP_CHANNEL;
    wc.ap.max_connection = 2;
    wc.ap.authmode = WIFI_AUTH_WPA2_PSK;
    esp_wifi_set_mode(WIFI_MODE_AP);
    esp_wifi_set_config(WIFI_IF_AP, &wc);
    strcpy(s_ip, "192.168.4.1");
    s_state = "ap";
    ESP_LOGI(TAG, "NET mode=ap ssid=%s ip=%s port=%d", (char *)wc.ap.ssid, s_ip, TCP_PORT);
}

static void retry_cb(void *)
{
    if (!s_ever_connected && s_attempts >= WIFI_STA_FALLBACK_ATTEMPTS) {
        event_post(EV_WARN, "wifi_fallback_ap", "station never connected after %u tries", (unsigned)s_attempts);
        start_ap(); // ponytail: runs in the esp_timer task; mode switch is quick
        return;
    }
    esp_wifi_connect();
}

static void on_event(void *, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (!strcmp(s_state, "ap")) return;
        auto *d = (wifi_event_sta_disconnected_t *)data;
        bool was_up = !strcmp(s_state, "sta_connected");
        s_state = "sta_connecting";
        strcpy(s_ip, "0.0.0.0");
        g_cnt.wifi_disconnects++;
        s_attempts++;
        uint32_t delay = 1000u << (s_attempts > 5 ? 5 : s_attempts - 1);
        if (delay > WIFI_BACKOFF_MAX_MS) delay = WIFI_BACKOFF_MAX_MS;
        if (was_up || s_attempts == 1 || s_attempts % 10 == 0)
            event_post(EV_WARN, "wifi_disconnected", "reason=%d attempt=%u retry_in=%ums", d->reason,
                       (unsigned)s_attempts, (unsigned)delay);
        esp_timer_start_once(s_retry_timer, (uint64_t)delay * 1000);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        auto *e = (ip_event_got_ip_t *)data;
        snprintf(s_ip, sizeof s_ip, IPSTR, IP2STR(&e->ip_info.ip));
        if (s_ever_connected) s_reconnects++;
        s_ever_connected = true;
        s_attempts = 0;
        s_state = "sta_connected";
        ESP_LOGI(TAG, "NET mode=sta ip=%s port=%d", s_ip, TCP_PORT);
        event_post(EV_INFO, "wifi_connected", "ip=%s reconnects=%u", s_ip, (unsigned)s_reconnects);
    }
}

void wifi_start(const app_cfg_t &cfg)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    esp_netif_create_default_wifi_ap();
    wifi_init_config_t ic = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&ic));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, nullptr));
    esp_timer_create_args_t ta = {};
    ta.callback = retry_cb;
    ta.name = "wifi_retry";
    ESP_ERROR_CHECK(esp_timer_create(&ta, &s_retry_timer));

    if (cfg.wifi_ap || !cfg.sta_ssid[0]) {
        start_ap();
    } else {
        wifi_config_t wc = {};
        strlcpy((char *)wc.sta.ssid, cfg.sta_ssid, sizeof wc.sta.ssid);
        strlcpy((char *)wc.sta.password, cfg.sta_pass, sizeof wc.sta.password);
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
        s_state = "sta_connecting";
        ESP_LOGI(TAG, "NET mode=sta ssid=%s connecting", cfg.sta_ssid);
    }
    ESP_ERROR_CHECK(esp_wifi_start()); // default power save (MIN_MODEM in STA); recorded, not tuned
}
