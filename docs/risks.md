# Risks

| # | Risk | Status (2026-10-15) | Mitigation |
|---|---|---|---|
| R1 | Quad PSRAM misconfiguration | Closed for unit #1: octal PSRAM found | `CONFIG_SPIRAM_MODE_OCT=y` in `sdkconfig.defaults` |
| R2 | OV3660 NO-EOI / timeouts | 0 failures in 2×1,000 QVGA captures at 20 MHz | Counters in firmware; 10 MHz XCLK fallback (`DEFAULT_XCLK_HZ` in config.h, or `set xclk_hz` at runtime). Watch at higher resolutions and over the soak. |
| R3 | Overheating (80–90 °C reports) | 1 h soak without heatsink, Wi-Fi streaming: plateau ~69 °C, max 71.9 °C (open desk, laptop USB). No heatsink available yet, so the heatsink run is BLOCKED; enclosures, sun or a warm room were not tested | 1 h soak with and without heatsink; thermal EVENT at 80 °C in Gate 2 |
| R4 | Brown-out / weak USB power | Not seen | Reset reason logged at boot |
| R5 | Venue Wi-Fi congestion | Untested | STA + SoftAP; U.FL antenna mandatory |
| R6 | Model recall on overhead/small people | Untested on live scenes | Document the mount (2–2.5 m, oblique) |
| R7 | Mixed OV2640/OV3660 units | Unit #1 = OV3660 | PID check at boot with a `sensor_mismatch` event; per-unit verify script |
| R8 | Per-stage `t_pre/t_infer/t_post` not exposed by `PedestrianDetect::run` | Closed (Gate 2) | `TimedPico` in vision.cpp repeats `DetectImpl::run`'s three steps with timestamps; same model and postprocessor. Re-check if esp-dl changes `DetectImpl::run` |
| R9 | IDF install folder name (v5.5.1) ≠ actual version (v5.5.3) | Noted | Pinned `idf: ==5.5.3`; setup docs to require 5.5.3 |
| R10 | Task watchdog starvation from a busy-looping vision task | Fixed in bring-up (`vTaskDelay(1)`) | Gate 2: vision task on core 1 with explicit yield policy |
| R12 | Wi-Fi send stalls up to 4.9 s (15% frames dropped in the 1 h soak) | Observed, not fixed | Drop-oldest absorbs it; send timeout 5 s is close, so a longer stall drops the client; reconnect verified on hardware in Gate 2 |
| R13 | `wifi_secrets.h` appearing is not seen by incremental builds | Mitigated: credentials can now be set via COMMAND + NVS | Still touch `config/settings.cpp` / fullclean after adding the file |
| R14 | A saved wrong SSID sends the board to SoftAP, where a PC on the venue network can't reach it | Known | Recovery: `esptool --port COMx erase_region 0x9000 0x6000` (erases NVS → compile-time defaults). Goes in flashing_and_recovery.md |
| R15 | Wi-Fi password sent in clear over TCP by `set sta_pass` | Accepted for a closed camp network | Not for production; HELLO never echoes it |
| R11 | Very small JPEGs (~3.6 KB) in the first live test | Closed: saved soak frames show a partly blocked, sideways scene; JPEG 2.7–9.7 KB over the soak | Mount camera upright for detection tests |
