/**
 * @file wifi_secrets.example.h
 * Template for wifi_secrets.h (git-ignored): copy it, then fill in a 2.4 GHz network.
 * Without wifi_secrets.h the board starts its own SoftAP "GMT-PF-xxxx" (password gmtcamp2026).
 */
#pragma once
constexpr char WIFI_STA_SSID[] = "your-2.4GHz-network";
constexpr char WIFI_STA_PASS[] = "your-password";
