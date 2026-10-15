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
