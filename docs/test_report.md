# Test report

Labels: VERIFIED ON HARDWARE / BUILD-VERIFIED ONLY / SIMULATED-UNIT-TESTED / NOT TESTED / BLOCKED.

## 2026-10-15: Gate 0 and Gate 1 steps 1–3 (unit #1, MAC 8c:bf:ea:8e:6d:d8)

Firmware: bring-up `firmware/main/main.cpp`. Versions are in `versions.md`. No heatsink. Powered from a laptop USB port. Room temperature not recorded.
Logs: `docs/logs/gate1_bringup_2026-10-15.log` (before the watchdog fix) and `docs/logs/gate1_bringup_wdtfix_2026-10-15.log`.

| Item | Result | Label |
|---|---|---|
| Reproducible build, pinned IDF + components, octal PSRAM | `idf.py build` clean, no warnings, app 2.6 MB / 7 MB partition | BUILD-VERIFIED ONLY (single machine) |
| Board facts | rev v0.2, flash 8 MB, PSRAM 8 MB found in octal mode, reset reason reported | VERIFIED ON HARDWARE |
| Camera sensor | PID **0x3660** (OV3660) at XCLK 20 MHz | VERIFIED ON HARDWARE |
| 1,000-capture test (QVGA JPEG q12, fb_count 2, PSRAM, grab-latest) | ok=1000, fail=0, NO-EOI=0, NO-SOI=0, FB-OVF=0; 27.76 fps capture-only; JPEG 3.6–3.7 KB | VERIFIED ON HARDWARE (two runs) |
| Detector on embedded still (640×480) | 3 boxes, run 4 below. First run t_detect 216 ms, then ~119.7 ms. t_decode ~77 ms at 640×480 | VERIFIED ON HARDWARE |
| Detector on live frames | Runs with no errors (627 frames: t_decode median 11.5 ms, t_detect median 117.6 ms, p95 118.0 ms). **0 boxes: no person in view during the run.** | Pipeline VERIFIED; detections NOT TESTED |
| Task watchdog | First run: IDLE0 starved every 5 s (warning only, no reset). Fixed with `vTaskDelay(1)` at 1 kHz tick. Second run: 0 warnings in 280 frames | VERIFIED ON HARDWARE |
| Die temperature reading | 51.9–56.9 °C during the 2-min run | VERIFIED ON HARDWARE (sensor reads; accuracy not checked) |
| Memory | internal free 252 KB (min 217 KB), PSRAM free 5.07 MB (min 4.18 MB), flat over 2 min | VERIFIED ON HARDWARE (short run only; soak pending) |

Still-image expected output (`pedestrian.jpg`, coordinates in 640×480):

```
score=0.884 x1=144 y1=187 x2=251 y2=464
score=0.879 x1=284 y1=195 x2=368 y2=462
score=0.786 x1=412 y1=226 x2=490 y2=396
```

## 2026-10-15: Gate 1.4 soak firmware (Wi-Fi + TCP sender)

Firmware: vision task on core 1, net task (TCP server :5000) on core 0, 4-slot PSRAM bundle pool, drop-oldest ready queue (depth 2), GMTP records JPEG_FRAME + DETECTIONS + JSON METRICS (provisional). STA if `wifi_secrets.h` exists, otherwise SoftAP.
Log: `docs/logs/gate1_softap_check_2026-10-15.log`.

| Item | Result | Label |
|---|---|---|
| Build | Clean, app 3.1 MB | BUILD-VERIFIED ONLY |
| SoftAP start | `GMT-PF-6DD9` at 192.168.4.1:5000 | VERIFIED ON HARDWARE (no client connected) |
| Vision task with Wi-Fi on, no client | 7.6 fps processed (76 frames / 10 s), t_detect 118 ms, t_proc 131 ms, 0 capture failures | VERIFIED ON HARDWARE |
| Memory after Wi-Fi start | internal free 121 KB (min 111 KB), PSRAM min 3.73 MB | VERIFIED ON HARDWARE (80 s only) |
| Die temperature with Wi-Fi on (SoftAP, no traffic) | 53 °C → 65 °C within ~40 s of Wi-Fi start | VERIFIED ON HARDWARE (trend; soak pending) |
| PC parser (`pc/protocol.py`): fragmentation, garbage prefix, CRC corruption, oversize | self-check passes | SIMULATED-UNIT-TESTED |
| `scripts/soak.py` against a fake local device: 399 frames, forced reconnect | 399/399 received, 0 seq gaps, 0 CRC errors, 0 missing pairs, frames + metrics.csv written | SIMULATED-UNIT-TESTED |
| Device CRC (`esp_rom_crc32_le`) matches zlib crc32 on the PC | not yet exercised | NOT TESTED |
| STA join of the configured network (2026-10-15 14:10) | `wifi_disconnected reason=201` (NO_AP_FOUND) ×10 in 35 s. PC scan sees that SSID on 5 GHz only. | BLOCKED (network is not 2.4 GHz) |
| TCP stream device → PC | blocked: PC internet is Wi-Fi only (GMT_THAI 5G, 5 GHz); the board needs a 2.4 GHz network | BLOCKED (waiting for `wifi_secrets.h`) |

## 2026-10-15 14:20–15:21: Gate 1.4 soak, run 1 of 2: NO heatsink (supervised)

Unit #1 (MAC 8c:bf:ea:8e:6d:d8), STA on GMT_THAI 2.4G (RSSI −46 to −72 dBm), PC on the same network, laptop USB power, U.FL antenna fitted, camera sideways on the desk with people moving in front of it. Room temperature not recorded.
Data: `docs/logs/soak_noheatsink_2026-10-15/` (summary.json, metrics.csv, host_stats.csv, serial.log, frames/).

| Exit criterion / measure | Result | Label |
|---|---|---|
| Resets | 0 unexpected (1 boot seen = the soak's own USB reset). 0 panics, 0 watchdog, 0 brown-out | VERIFIED ON HARDWARE |
| Internal heap | `int_min` fell 113 KB → 75.2 KB in the first 5 min (during the first Wi-Fi send stalls), then **flat for the remaining 55 min**. `int_free` 85–124 KB throughout, 110 KB at the end | VERIFIED ON HARDWARE: bounded |
| PSRAM | `ps_min` 3,725,704 B, constant all hour | VERIFIED ON HARDWARE: bounded |
| Capture | 26,591 frames processed, cap_fail 0, NO-EOI 0, NO-SOI 0, FB-OVF 0, decode fail 0 | VERIFIED ON HARDWARE |
| Die temperature | 56.9 °C at start, ~69 °C plateau after ~6 min, **max 71.9 °C**. Thermal threshold (80 °C) never reached | VERIFIED ON HARDWARE |
| Stream integrity (PC) | 22,553 frames received, 0 CRC errors, 0 seq gaps, 0 resyncs, 0 missing pairs, 0 reconnects, 0 Wi-Fi disconnects | VERIFIED ON HARDWARE |
| Device CRC matches zlib | yes (0 CRC errors over 3.6 h-equivalent of records) | VERIFIED ON HARDWARE |
| Throughput | processed ~7.3 fps on the device, **received 6.26 fps** on the PC (mean 52.9 KB/s). tx_dropped 4,009 (15%): the drop-oldest queue doing its job | VERIFIED ON HARDWARE |
| Send stalls | per-bundle `t_send` usually 2–40 ms, but 197 one-second samples above 1 s, max 4.92 s (just under the 5 s send timeout). 63 of ~360 ten-second windows below 5 fps received | VERIFIED ON HARDWARE (cause not investigated: likely Wi-Fi airtime/retries; a student-experiment topic, not fixed) |
| Device stage times | t_detect 118–126 ms, t_proc 131–140 ms | VERIFIED ON HARDWARE |
| Live detection | frame 4476 (600 s): 1 box, score 0.82, x 102–319, y 3–239 QVGA px, covering the person on the right of the frame. Frame 22165: 1 box, 0.73 | VERIFIED ON HARDWARE (by eye on one saved frame; camera was sideways) |

Minor: the device sent 3,125 METRICS records in 3,601 s, because METRICS go out between bundle sends and are delayed during send stalls. Fine for the soak; Gate 2's METRICS cadence should not depend on bundle sends.

**Gate 1.4 verdict (run 1):** passes the exit criteria (no resets, memory bounded, no persistent capture failures).

**Run 2 (with heatsink): BLOCKED.** No heatsink is available (GMT, 2026-10-15). The handoff requires heatsinks on all camp boards, so this is a procurement item. Re-run `scripts/soak.py --label heatsink` once one is fitted. Run 1 is the worst-case thermal reference: max 71.9 °C on an open desk.

### Not done yet
- Gate 1.3 with a person in view. Gate 1.4 one-hour soak with Wi-Fi sender (needs a heatsink decision and supervision).
- Box-to-QVGA mapping checked against a saved frame.

## 2026-10-15: Gate 2 device application (unit #1, branch `gate2-device-app`, fw `bed9ce0-dirty`)

Firmware split into modules (`firmware/main/`: settings, camera, vision, net, wifi, protocol, commands, metrics, events, grid, thermal). Protocol: `docs/protocol.md`. Tested with `pc/test_client.py` over GMT_THAI 2.4G, camera on the desk (no person in view for most runs, so boxes are mostly 0).

| Item | Result | Label |
|---|---|---|
| Build | clean, no warnings | BUILD-VERIFIED ONLY |
| Boot: board facts, NVS settings, 1000-capture test, self-test on still | capture 1000/1000, still → the same 3 boxes as Gate 1 | VERIFIED ON HARDWARE |
| Per-stage timing, QVGA full_stream | t_wait_frame 0.15 ms, t_decode 13.9 ms, **t_pre 8.5 ms, t_infer 110.4 ms, t_post 0.21 ms**, t_grid 4 µs, t_copy 0.26 ms, t_enqueue 5 µs, t_proc 133 ms; ~7 fps processed | VERIFIED ON HARDWARE |
| HELLO | device_id gmt-8e6dd8, fw_version from git, IDF/esp-dl/camera/model versions, sensor_pid 0x3660, full config without the password | VERIFIED ON HARDWARE |
| Bundle pairing (20 s, full_stream) | 129/129 JPEG_FRAME + DETECTIONS pairs, 0 mismatches, 0 seq gaps, 0 CRC errors; 16 frame_id gaps = device `tx_dropped` 16 | VERIFIED ON HARDWARE |
| Reconnect without reboot | 4 sessions in 24 s: each got HELLO, 172/172 pairs, `client_lost`/`client_connected` events | VERIFIED ON HARDWARE |
| Queue drops under an artificially slow client (reader capped at ~10 KB/s) | device `tx_dropped` +46 in 25 s, `queue_drop_burst` event, 27/27 delivered bundles still paired; inference unaffected (7.4 fps processed, t_enqueue max 18 µs, t_proc max 138 ms) | VERIFIED ON HARDWARE |
| Modes switched at runtime | metadata_stream: DETECTIONS only, 7.9 fps · detect_no_stream: METRICS only, 8.0 fps processed · camera_only: t_wait_frame mean 35 ms (≈ 28 fps), 0 processed · inference_only: 3 boxes per run, `inference_only_done` after 100 runs, t_decode 77.7 ms (640×480) · full_stream VGA q10: camera restart + grid reset events, t_decode 53 ms, 5.2 fps processed / 3.4 fps sent | VERIFIED ON HARDWARE |
| Mode identified in logs | `mode` in every METRICS, HELLO config, `mode_changed` event | VERIFIED ON HARDWARE |
| COMMAND validation | out-of-range value, unknown mode, unknown cmd, non-JSON → `cmd_error` each; a mixed valid+invalid `set` applied nothing | VERIFIED ON HARDWARE |
| metrics_verbose | FRAME_TIMING record per bundle (33/33) | VERIFIED ON HARDWARE |
| Presence grid: accumulate / reset / request | counts accumulate; `reset_grid` → 0 counts; `get_grid` answered; reset on frame-size change | VERIFIED ON HARDWARE (accumulation with real people: few boxes in view, total 16 over 1,430 frames) |
| PING/PONG clock offset | best RTT 16.4 ms → offset reported ± 8.2 ms | VERIFIED ON HARDWARE |
| Settings persist in NVS | `metrics_interval_ms=2000` + save + reboot → first HELLO after reboot shows 2000 (reset_reason SW); restored to 1000 | VERIFIED ON HARDWARE |
| Thermal warning event | threshold set to 40 °C → `thermal_warning` (die 52.9 °C); back to 80 → `thermal_ok` | VERIFIED ON HARDWARE (via lowered threshold; 80 °C never reached naturally) |
| Wi-Fi STA | joins GMT_THAI 2.4G, RSSI −55 dBm | VERIFIED ON HARDWARE |
| SoftAP fallback | nonexistent SSID saved → 8 tries with capped backoff (~140 s) → `wifi_fallback_ap`, AP `GMT-PF-6DD9` up (serial log). Recovered with `esptool erase_region 0x9000 0x6000` → back on STA | VERIFIED ON HARDWARE (no client connected to the AP) |
| STA reconnect after the AP drops out | not exercised (couldn't take the router down) | NOT TESTED |
| Protocol parser (fragmentation, corruption, oversize, grid/detections/JSON decode) | `python pc/protocol.py` passes | SIMULATED-UNIT-TESTED |

Observations for students (not fixed, by design): per-bundle `t_send` averages ~78 ms for ~9 KB JPEGs (≈ 115 KB/s), so sent fps sits slightly below processed fps and occasional drops occur. At VGA the network, not inference, limits sent fps. Wi-Fi power save is left at the IDF default (MIN_MODEM).
