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
| Device CRC (`esp_rom_crc32_le`) matches zlib crc32 on the PC | not yet exercised at this point; superseded: 0 CRC errors over the 1 h soak below | NOT TESTED here, VERIFIED later |
| STA join of the configured network (2026-10-15 14:10) | `wifi_disconnected reason=201` (NO_AP_FOUND) ×10 in 35 s. PC scan sees that SSID on 5 GHz only. | BLOCKED (network is not 2.4 GHz); superseded: STA on GMT_THAI 2.4G worked from 14:20 |
| TCP stream device → PC | blocked: PC internet is Wi-Fi only (GMT_THAI 5G, 5 GHz); the board needs a 2.4 GHz network | BLOCKED at this point; superseded by the soak below |

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

## 2026-10-15: Gate 3 PC viewer, logger, tracker, replay, tests (branch `gate2-device-app`)

PC stack (`pc/requirements.txt`): Python 3.12.4, numpy 2.4.6, opencv-python 4.14.0.94, pytest 8.4.2, in `pc/.venv`.
Modules: `protocol.py` (framing/codecs), `pipeline.py` (frame_id join, stats, tracker feed, logging), `tracker.py`, `heatmap.py`, `clock_sync.py`, `logger.py`, `replay.py` (+ synthetic generator), `viewer.py` (OpenCV), `test_client.py`.

| Item | Result | Label |
|---|---|---|
| `pytest pc/tests`: parser (fragmentation at every chunk size, corruption + resync, oversize, truncation, decoders); pipeline (pairing by frame_id, out-of-order halves, never DETECTIONS[n+1] on frame n, missing JPEG / missing DETECTIONS, metadata mode, seq gaps, reconnect); grid cell mapping (matches firmware) + normalization; tracklet association, gating, expiry, jitter suppression, step accumulation; segment rasterization; CSV/JSONL schema; JPEG-only-when-recording; logged run replays identically | **27 passed** | SIMULATED-UNIT-TESTED |
| `samples/synthetic_run/` replays without hardware | 68 pairs, 1 missing pair, 1 frame_id gap, 0 seq gaps (all as designed); committed sample byte-identical to the generator | SIMULATED-UNIT-TESTED |
| Viewer on the synthetic replay (headless snapshot) | frame + boxes + scores + frame_id; movement overlay follows the three walkers; legend with units; limitations printed | SIMULATED-UNIT-TESTED |
| Viewer on the live device, 20 s, logging on, recording off | 119 pairs, 0 seq gaps, 0 CRC errors, 0 missing pairs; 25 frame_id gaps = device drops during Wi-Fi send stalls (device t_send window mean up to 418 ms, RSSI −55 to −64); host draw 1.5–6.5 ms/frame; clock bound ±11 ms; capture→display 183–1,500 ms ±11 ms (varies with the stalls) | VERIFIED ON HARDWARE |
| Run logs from the live session | `runs/<time>_gmt-8e6dd8/`: hello.json, detections.jsonl (119), metrics.csv (fixed columns), events.jsonl, grid_snapshots.csv, host_stats.csv, stream.gmtp; `frames/` empty (recording off) | VERIFIED ON HARDWARE |
| Replay of that live run | same counters as live (270 records, 119 pairs, 25 gaps) | VERIFIED ON HARDWARE (data from hardware, replay on PC) |
| Presence overlay on live data | renders; almost no detections in view (max 0.0002 detections/frame/cell over 5,014 frames) | VERIFIED ON HARDWARE (rendering only; not validated with people walking) |
| Interactive keys (o/r/g/m/s/c/q) in the window | handlers written; only exercised headless (no key presses) | NOT TESTED |
| Live recording on (JPEGs to disk) | covered by unit test only; not run live to avoid saving images of people in the office | SIMULATED-UNIT-TESTED |

## 2026-10-15: Gate 4 docs and scripts (baseline data NOT collected)

| Item | Result | Label |
|---|---|---|
| `scripts/flash_all.ps1` → `verify_board.py --all --flash` on unit #1 | merged image flashed (hash verified); MAC 8c:bf:ea:8e:6d:d8, rev v0.2, 8 MB flash, 8 MB PSRAM, sensor 0x3660, capture test 1000/1000, self-test 3 boxes, IP 192.168.1.248. Result **FAIL, correctly**: image built from an uncommitted tree (`3a81649-dirty`) | VERIFIED ON HARDWARE |
| Re-run from the clean Gate 4 commit (`2959028`, `idf.py reconfigure` + build + merge-bin) | **PASS**: fw `2959028`, sensor 0x3660, 8 MB PSRAM, 8 MB flash, capture 1000/1000 (27.8 fps), self-test 3 boxes, IP 192.168.1.248, flash hash verified | VERIFIED ON HARDWARE |
| `flash_all.sh` | thin wrapper, same script | NOT TESTED (no Linux machine) |
| `collect_baseline.py` smoke test (1 repeat × 5 modes, 8 s each, 3 s warm-up; output discarded, *not a baseline*) | all 5 trials completed; summary.csv + baseline.json + per-trial run folders written; detected the `-dirty` firmware and warned | VERIFIED ON HARDWARE (script function only) |
| Baseline: reference scene, 3 repeats × 5 modes × 60 s, heatsink fitted | — | BLOCKED (no heatsink; reference scene not set up) |
| Tag `baseline-v1` | — | BLOCKED (waits for the baseline) |
| Docs: README, setup_windows, setup_linux, flashing_and_recovery, experiments, student_quickstart, mentor_notes | written | Windows steps follow what was run on the reference PC; **setup_linux NOT TESTED**; erase_region and SoftAP recovery VERIFIED ON HARDWARE (Gate 2) |
| "Students can run an unmodified baseline from the quickstart alone" | — | NOT TESTED (needs a student dry run) |

## 2026-10-15 17:07–18:36: preliminary performance characterization, fw `2959028` (see `performance_report.md`)

Not the reference baseline: no heatsink, uncontrolled desk scene, room temperature not measured.

| Item | Result | Label |
|---|---|---|
| Mode characterization, 3 repeats × 5 modes × 60 s (`docs/measurements/20261015_1707_…`) | all 15 trials complete; e.g. full_stream 7.33 ± 0.03 fps processed, t_infer 111.6 ms, t_proc 135.1 ms; metadata_stream 0 drops; camera_only 27.65 fps | VERIFIED ON HARDWARE (preliminary conditions) |
| **1 h soak on the final firmware** (`docs/measurements/soak_20261015_1727_…`) | 0 unexpected resets, 0 Wi-Fi disconnects, 0 capture timeouts, 0 seq/CRC errors, 0 missing pairs; int_min flat from minute 23 (66,011 B), ps_min flat from minute 5; max die temp 73.9 °C (no heatsink); 6.1 % of frames dropped by drop-oldest | VERIFIED ON HARDWARE: memory bounded on the final firmware |
| End-to-end capture → display, 180 s (`docs/measurements/latency_20261015_1833_viewer/`) | median 158 ms, p90 291 ms, max 646 ms, ± 6.2 ms median clock bound | VERIFIED ON HARDWARE |
| `collect_baseline.py` trial-boundary fix (false missing pair) | 2 × 2 smoke trials: every trial starts on a frame boundary, 0 missing pairs | VERIFIED ON HARDWARE |
| `collect_baseline.py` final restore fix (command lost on immediate close) | fix written: waits for the confirming HELLO | BUILD-VERIFIED ONLY (the code path runs at the end of a full baseline; not re-run yet) |

## 2026-10-16: code clean-up (self-documenting names, docstrings instead of comments), re-verified

No intended behavior change. Wire names (counters, settings keys, JSON fields, log formats) were kept.

| Item | Result | Label |
|---|---|---|
| Firmware build after refactor | clean, 0 warnings | BUILD-VERIFIED ONLY |
| `verify_board.py` (refactored) on unit #1 | MAC, rev, 8 MB / 8 MB, sensor 0x3660, capture 1000/1000, self-test 3 boxes, IP; FAIL only for `-dirty` (uncommitted tree), as designed | VERIFIED ON HARDWARE |
| full_stream 15 s with ping (`test_client.py`, refactored) | 110/110 pairs, 0 mismatch / seq / CRC errors; ±5.4 ms clock bound; t_pre 8.8, t_infer 111.3, t_post 0.2, t_proc 135.2 ms | VERIFIED ON HARDWARE |
| Commands and modes | metadata_stream, inference_only (t_proc 199.6 ms), camera_only, back to full_stream; invalid value / unknown key / unknown cmd rejected with the same messages, nothing applied | VERIFIED ON HARDWARE |
| Slow reader | drops counted (tx_dropped 8 = frame_id gaps 8), delivered bundles all paired | VERIFIED ON HARDWARE |
| Viewer live and replay (headless) | live: 78 pairs, 0 seq gaps; replay counters identical to before | VERIFIED ON HARDWARE / SIMULATED-UNIT-TESTED |
| `collect_baseline.py` (refactored) incl. final restore | trials complete; board back in full_stream with infer_only_runs 100 afterwards (restore fix now exercised on hardware) | VERIFIED ON HARDWARE |
| `soak.py` (refactored), 2 min | reset + IP from serial, 859 frames, 0 resets / seq / CRC / missing pairs | VERIFIED ON HARDWARE |
| Synthetic generator | output byte-identical to the committed sample | SIMULATED-UNIT-TESTED |
| `report_baseline.py` | tables identical to the previous version's output | SIMULATED-UNIT-TESTED |
| `pytest pc/tests` | 34 passed (tests split per behavior; `record_size` test added) | SIMULATED-UNIT-TESTED |

## 2026-10-16: folder restructure (firmware modules in folders, `pc/main.py` as the single entry point), re-verified

`app.h` split into per-module headers; firmware sources grouped into `config/ vision/ network/ telemetry/ utils/ assets/`; PC code grouped into `ui/ analysis/ connection/ recording/ tools/ utils/`; `scripts/` folded into `pc/tools/` as `pc/main.py` commands. `wifi_secrets.h` moved to `firmware/main/config/` and `.gitignore` updated (checked with `git check-ignore`). The synthetic sample was regenerated because its built-in note now names `pc/main.py make-synthetic` (+13 bytes; all counters unchanged).

| Item | Result | Label |
|---|---|---|
| Firmware build | clean, 0 warnings; Wi-Fi credentials still compiled in from the new location | BUILD-VERIFIED ONLY |
| `pc/tools/flash_all.ps1` → `main.py verify-board` | flashed (hash verified); every hardware check passed; FAIL only for `-dirty` | VERIFIED ON HARDWARE |
| `main.py check` (ping, `--set`, bad value) | 86/86 pairs, 0 seq/CRC errors, ±9.5 ms; mode switches; `cmd_error` for an out-of-range value | VERIFIED ON HARDWARE |
| `main.py viewer` live and replay | live 73 pairs, 0 seq gaps, 0 missing; replay counters unchanged | VERIFIED ON HARDWARE / SIMULATED-UNIT-TESTED |
| `main.py baseline`, `serial-log`, `soak` (1 min) | complete; soak writes to repo `runs/`, 411 frames, 0 resets/seq/CRC/missing | VERIFIED ON HARDWARE |
| `main.py report` | identical tables to the original script | SIMULATED-UNIT-TESTED |
| `pytest pc/tests` | 34 passed | SIMULATED-UNIT-TESTED |

## 2026-10-16: readability pass (no short forms, C++ Core Guidelines, PEP 8, type hints), re-verified

Behaviour, wire format, log formats and error texts were kept; only the way the code is written changed. Firmware: no ternaries or one-line `if`s, `enum class` for modes / record types / severities / timing stages (lowercase names matching the laptop's), `stage_times_t` and `mac_address_t` types, packed header structs instead of byte offsets, named casts only, `nullptr`, named constants for pins, units, buffer sizes and the "set" limits (now in `config.h`), no `#define` (component versions come from a CMake-generated `component_versions.h`; `wifi_secrets.h` and its example now use `constexpr`; the git-ignored `wifi_secrets.h` on the mentor laptop was converted in place). The NVS settings layout is byte-compatible (`app_mode_t` keeps `uint8_t`), so saved settings still load. PC: no walrus, inline `if`, `or` defaults, lambdas, `**`/`|` merges, `*rest` unpacking or filtering comprehensions; PEP 8 at 79 columns (pycodestyle + black) and type hints on every function (mypy `disallow_untyped_defs`); checker settings in `pc/pyproject.toml`, checker versions pinned in `requirements.txt`. `samples/synthetic_run/README.md` regenerated from the repo root: the committed copy named `../samples/...` paths (generated from inside `pc/`); `stream.gmtp` unchanged.

| Item | Result | Label |
|---|---|---|
| Firmware build | clean, 0 warnings; `component_versions.h` holds esp-dl 3.3.13, esp32-camera 2.1.8, pedestrian_detect 0.3.2 | BUILD-VERIFIED ONLY |
| `main.py verify-board --flash` (COM3) | flashed (hash verified); sensor 0x3660, 8 MB/8 MB, self-test 3 boxes, capture fail 0, joined Wi-Fi with the `constexpr` secrets; FAIL only for `-dirty` (`afc6768-dirty`) | VERIFIED ON HARDWARE |
| `main.py check` (ping, `--set`, out-of-range value, `get_grid`, `metrics_verbose`) | 45/45 pairs, 0 mismatch/seq/CRC/frame_id gaps; FRAME_TIMING arrives with all stage names; error text `jpeg_quality: 4..63` unchanged; ±7.2 ms clock | VERIFIED ON HARDWARE |
| `main.py viewer` live | 57 pairs, 0 seq gaps, 0 missing; 7.7 fps processed and sent | VERIFIED ON HARDWARE |
| `main.py baseline` (full_stream, metadata_stream, inference_only; 5 s each) | complete; 0 CRC errors, 0 missing pairs, 0 frame_id gaps | VERIFIED ON HARDWARE |
| `main.py soak` (1 min) | 61 s, 363 frames, 0 seq/CRC/missing, 0 reconnects, 0 unexpected resets | VERIFIED ON HARDWARE |
| Synthetic sample | `stream.gmtp` byte-identical to the generator output | SIMULATED-UNIT-TESTED |
| `main.py replay samples/synthetic_run` | 155 records, 68 pairs, 1 missing pair, 1 frame_id gap, 121 movement crossings (unchanged) | SIMULATED-UNIT-TESTED |
| `main.py report` (Oct 15 baseline + soak) | byte-identical to the output of the previous code | SIMULATED-UNIT-TESTED |
| `pytest pc/tests` | 34 passed | SIMULATED-UNIT-TESTED |
| `pycodestyle`, `black --check`, `mypy` | no issues in 29 files | SIMULATED-UNIT-TESTED |
| Viewer keys pressed by a person, STA reconnect after AP loss | not re-tested | NOT TESTED |

## 2026-10-16: simplification pass (audit items 1-9), re-verified

`soak` now matches pictures with boxes and counts losses with the same `Pipeline` as the viewer (its own seq-gap and pairing code removed; `frames/*.json` now holds the completed frame: frame_id, w, h, boxes, t_capture_us). Firmware: `std::clamp` replaces two hand-written clamps; the duplicate list of numeric setting keys is gone (non-numeric keys are checked first, then `apply_number`, which rejects unknown keys); `send_json_text` folded into `send_json`; one `MICROSECONDS_PER_SECOND` constant. PC: one `protocol.MODES` list instead of three copies; synthetic records built with a plain list; `pc/tools/flash_all.ps1` / `.sh` deleted, docs now give `python pc/main.py verify-board --all --flash`.

| Item | Result | Label |
|---|---|---|
| Firmware build | clean, 0 warnings | BUILD-VERIFIED ONLY |
| `main.py verify-board --all --flash` | found COM3 by itself, flashed (hash verified), every hardware check passed; FAIL only for `-dirty` | VERIFIED ON HARDWARE |
| `main.py check`: unknown key, out-of-range value, inference_only | `unknown key 'bogus'` and `xclk_hz: 5000000..24000000` unchanged; inference_only 19 frames, 57 boxes (3 per frame, as the self-test), 0 CRC/seq errors | VERIFIED ON HARDWARE |
| `main.py soak` (1 min, frame every 20 s) | 0 seq/CRC/missing, 0 reconnects, 0 unexpected resets; 3 frames saved. 222 frames (3.6 fps): for about 50 s the board's t_send was 400-600 ms and it dropped bundles, then recovered to 7.6-7.9 fps sent with the same receiver; `Pipeline.feed` costs about 8 us per record, so this was the Wi-Fi, not the receiver | VERIFIED ON HARDWARE |
| Synthetic `stream.gmtp`, replay counters, `report` tables | identical | SIMULATED-UNIT-TESTED |
| `pytest`, `pycodestyle`, `black --check`, `mypy` | 34 passed; no issues | SIMULATED-UNIT-TESTED |

## 2026-10-16: Wi-Fi drop and reconnect (simulated outage), handover clean-up

PC: the viewer and soak now treat 10 s without data as a dead connection and reconnect (a dropped router often leaves the TCP connection silent instead of closed, and both used to wait on it forever); soak also follows the board to a new IP printed on serial. Handover: answer-revealing internal records are `export-ignore` (`.gitattributes`), hinting sentences removed from shipped docs and code comments, Windows only (`setup_linux.md` removed), one board at a time (`verify-board --port`, `--all` removed), no heatsink, checkers moved to `requirements-dev.txt`, office SSID removed from `setup_windows.md`. Package dry run (`git archive` of the working tree): 101 entries, no internal records, images (except the Espressif self-test still), firmware images or office Wi-Fi strings.

The office router could not be switched off and nobody was on site, so the outage was made by a temporary test build (not kept): 120 s after first joining, the board pointed its station config at a network that does not exist for 90 s, then back. The real disconnect / backoff / reconnect code ran unchanged.

| Item | Result | Label |
|---|---|---|
| `pytest pc/tests` (new `test_reconnect.py`: a fake board goes silent with the connection open) | 36 passed; both new tests fail without the fix | SIMULATED-UNIT-TESTED |
| Board: drop while connected | `wifi_disconnected reason=8` at 159.9 s, then 7 retries with backoff; `client_lost errno=113` at once | VERIFIED ON HARDWARE (simulated outage) |
| Board: network back | config restored at the next retry (265.4 s, 15.5 s after the 90 s ended, because of the backoff; worst case 30 s); IP again 2.0 s later; `wifi_connected reconnects=1`; no SoftAP fallback, no reset | VERIFIED ON HARDWARE (simulated outage) |
| Laptop (`soak`): reconnect | 14 failed connects during the outage, reconnected 0.8 s after the board was back; afterwards 7.4 fps, 0 seq gaps, 0 CRC errors, 0 missing pairs over the 7 min run | VERIFIED ON HARDWARE (simulated outage) |
| Normal firmware restored | rebuilt (0 warnings), flashed, boot log has no outage code; every hardware check passed, FAIL only for `-dirty` | VERIFIED ON HARDWARE |
| A real router power cycle; the viewer window through an outage | not done (office router in use, nobody on site); the viewer uses the same watchdog, covered by `test_reconnect.py` | NOT TESTED |
| Viewer keys pressed by a person | not done | NOT TESTED |
