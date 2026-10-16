# Architecture

Design source: handoff v2, §5. Wire format: `protocol.md`. This file records what is built.

```text
core 1: vision_task (vision/vision.cpp)                  core 0: net_task (network/net.cpp)
  esp_camera_fb_get                 t_wait_frame           TCP server :5000, one client
  [full_stream] memcpy JPEG -> pool  t_copy                 HELLO on connect / after set
  esp_camera_fb_return (right after copy, or after decode)  bundles: JPEG_FRAME + DETECTIONS
  sw_decode_jpeg -> RGB888 (PSRAM)   t_decode               PRESENCE_GRID every N bundles
  TimedPico: preprocess | model | postprocess               METRICS every metrics_interval_ms
                        t_pre  t_infer  t_post              EVENTs (queued, depth 16)
  grid_add (bottom-center)           t_grid                 COMMANDs -> network/commands.cpp
  enqueue bundle -> ready_q (depth 2) t_enqueue  ───────►   socket write per bundle  t_send
                                     t_proc = fb_get return -> enqueued
```

## Modules (`firmware/main/`)
Only `main.cpp` (boot sequence) sits at the top; everything else is grouped by job, each `.cpp` next to its header.

| Folder | Files | Role |
|---|---|---|
| `config/` | `config.h` | compile-time defaults (one place for all knobs) and the mode list |
| | `settings.h/.cpp` | runtime config = defaults + NVS blob; validated `set`; `save` |
| | `wifi_secrets.example.h` | template for the git-ignored `wifi_secrets.h` |
| `vision/` | `vision.h/.cpp` | vision task: `TimedPico` (per-stage detector timing), five modes, backpressure policy |
| | `bundle.h` | `box_t`, `bundle_t`, the bundle pool and its two queues |
| | `camera.h/.cpp` | XIAO pin map, init/deinit, PID check, warm-up, boot capture test, NO-EOI counting via log hook |
| | `grid.h/.cpp` | 20×15 presence grid |
| `network/` | `net.h/.cpp` | net task: TCP server, HELLO, bundle/grid/metrics/event sending, command receive |
| | `protocol.h/.cpp` | record framing, CRC, incoming parser with resync |
| | `commands.h/.cpp` | COMMAND dispatch |
| | `wifi.h/.cpp` | STA with capped backoff, SoftAP, fallback to SoftAP |
| `telemetry/` | `metrics.h/.cpp` | counters, stage list, per-window min/mean/max/p95, METRICS JSON |
| | `events.h/.cpp` | EVENT queue (also logged to serial) |
| | `thermal.h/.cpp` | die temperature, warning with hysteresis (no throttling) |
| `utils/` | `device.h/.cpp` | reset reason, MAC, cJSON-in-PSRAM hook |
| `assets/` | `pedestrian.jpg` | embedded still for the self-test and `inference_only` |

Includes are written from `main/`, e.g. `#include "telemetry/metrics.h"`.

## Decisions
- **Buffers:** 4 bundles × 64 KiB JPEG in PSRAM, allocated at boot. There's no per-frame malloc on the streaming path; the exceptions are the decoded RGB image (esp-dl allocates and frees it each frame) and the cJSON objects (PSRAM, about once a second).
- **Backpressure:** drop-oldest in `full_stream`. Metadata modes wait up to 1 s, then drop. With no client, bundles are discarded.
- **Only inferred frames are streamed.** `infer_every_n` > 1 skips frames entirely, so JPEG and boxes always match.
- **Boxes:** pixels of the source frame (`frame_px`), clamped. For QVGA that is QVGA px.
- **JSON** for low-rate records (HELLO, METRICS, EVENT, COMMAND, PONG, FRAME_TIMING); **binary** for JPEG_FRAME, DETECTIONS and PRESENCE_GRID.
- **Detector stage timing:** `TimedPico` subclasses `pedestrian_detect::Pico` and repeats the three calls of `dl::detect::DetectImpl::run` (esp-dl 3.3.13) with timestamps. It is the same model, thresholds and postprocessor, with no esp-dl changes.
- **JPEG decode:** esp-dl `sw_decode_jpeg` (esp_new_jpeg), RGB888.
- **Model** in flash rodata, copied to PSRAM at load.
- **FreeRTOS tick 1 kHz;** the vision loop yields 1 tick per iteration so IDLE1 feeds the task watchdog.
- **Wi-Fi power save** stays at the IDF default (MIN_MODEM). It is recorded, not tuned.
- **Versions in HELLO** are read at build time from `managed_components/*/idf_component.yml` (CMake), so they can't drift from what was built.

## PC side (`pc/`)
`pc/main.py` is the only entry point: `python pc/main.py <command>` (run it without a command for the list).

| Folder | Files | Role |
|---|---|---|
| `ui/` | `viewer.py` | the window: live or replay source, keyboard controls, headless snapshot |
| `analysis/` | `pipeline.py`, `tracker.py`, `heatmap.py` | frame_id join and loss counters, tracklets, both heatmaps |
| `connection/` | `protocol.py`, `clock_sync.py` | GMTP framing and codecs, PING/PONG clock offset |
| `recording/` | `logger.py`, `replay.py`, `synthetic.py` | run folders, replay, the synthetic sample |
| `tools/` | `check.py`, `baseline.py`, `report.py`, `soak.py`, `verify_board.py`, `serial_log.py` | commands for checks, experiments and flashing |
| `utils/` | `paths.py`, `usb_serial.py` | repository paths, USB reset and boot-log parsing |
| `tests/` | `test_*.py` | pytest suite, no hardware needed |

```text
LiveSource (socket thread) ─┐                     ┌─ RunLogger: runs/<time>_<device>/ (always; JPEGs only when recording)
ReplaySource (stream.gmtp) ─┴─► Parser ─► Pipeline ┼─ frame_id join ─► latest frame ─► viewer render (OpenCV)
                                                   ├─ Tracker (ephemeral NN tracklets) ─► MovementGrid (PC metric)
                                                   ├─ PRESENCE_GRID from device (device metric)
                                                   └─ ClockSync (PING/PONG, min-RTT) ─► capture→display ± RTT/2
```
- The viewer and replay share `Pipeline`, so a replayed run gives the same counters as the live session (tested).
- `stream.gmtp` keeps every record. With recording off, JPEG_FRAME keeps its 16-byte header with an empty image, so seq and frame_id stay continuous.
- Movement tracker defaults: gate = max(20 px, 400 px/s × Δt), expiry after 3 missed frames, minimum step 8 px (anchor-based, so slow walkers still count). These are knobs in `Tracker(...)`, not tuned values.
