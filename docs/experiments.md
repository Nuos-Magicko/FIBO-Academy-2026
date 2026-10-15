# Experiments: definitions, modes, knobs, log template

This system is a **baseline to investigate**, not a solution to improve toward a target. Every number below is measured on the device or the PC with the boundaries stated here. If a boundary is unclear, read the code it names.

## 1. Timing stages (device, µs, `esp_timer_get_time()`)
Defined in `firmware/main/app.h` (`stage_t`); measured in `vision.cpp` and `net.cpp`.

| Field | Starts | Ends |
|---|---|---|
| `t_wait_frame` | before `esp_camera_fb_get()` | it returns a frame (blocked waiting for the camera) |
| `t_copy` | before `memcpy` of the JPEG into the transmit pool (`full_stream` only) | after the copy |
| `t_decode` | before `dl::image::sw_decode_jpeg` | RGB888 image ready |
| `t_pre` | detector preprocess start (resize/quantize to 224×224) | preprocess done |
| `t_infer` | `model->run()` start | model done (the neural network only) |
| `t_post` | postprocess start (decode boxes, NMS) | result list ready |
| `t_grid` | before the presence-grid update | after it |
| `t_enqueue` | before handing the bundle to the network queue (incl. any wait) | queued |
| `t_proc` | `esp_camera_fb_get()` **returned** | bundle queued: the device processing latency for one frame |
| `t_send` | net task starts writing a bundle to the socket | last byte accepted by the TCP stack |

- The camera buffer is returned to the driver right after `t_copy` (`full_stream`) or right after `t_decode` (other modes).
- The very first detector run after boot is slower (about 216 ms vs about 120 ms). The boot self-test absorbs it.

## 2. Throughput is not latency
- **Processed fps** (`fps_processed`): frames the device fully processed per second.
- **Sent fps** (`fps_sent`): bundles the device finished writing to the socket per second.
- **Capture fps**: frames delivered by the camera (`counters.cap_ok` change / time). Only in `camera_only` is this the camera's own rate.
- **Received / display fps** (PC `host_stats.csv`): frames the PC completed and drew.

A low `t_proc` does not mean high fps, and high processed fps does not mean the PC sees every frame. `frame_id` gaps on the PC count frames the device processed but dropped (drop-oldest) before sending.

## 3. End-to-end latency
Device and PC clocks are not synchronized. The PC estimates the offset with PING/PONG and keeps the smallest round trip: `offset = t_dev − (t_host + rtt/2)`, uncertainty **±rtt/2**. "Capture → display" in the viewer uses that offset and is always reported with its ± band. Don't quote an end-to-end number without the band, or one computed from raw timestamps of the two clocks.

## 4. Modes
| Mode | Device does | Sent over Wi-Fi |
|---|---|---|
| `camera_only` | capture + return | METRICS, EVENT |
| `inference_only` | decode + detect the embedded 640×480 still, `infer_only_runs` times | DETECTIONS (no JPEG), METRICS, EVENT |
| `detect_no_stream` | full live pipeline (no JPEG copy) | METRICS, EVENT |
| `metadata_stream` | full live pipeline | DETECTIONS, PRESENCE_GRID, METRICS, EVENT |
| `full_stream` (default) | full live pipeline + JPEG copy | JPEG_FRAME + DETECTIONS, PRESENCE_GRID, METRICS, EVENT |

Switch with the viewer key **m** or `{"cmd":"set","values":{"mode":"…"}}`. The current mode is in every METRICS record, in HELLO (`hello.json`), and in `mode_changed` events.

## 5. Knobs (`{"cmd":"set","values":{…}}`, `save` to keep)
`frame_size` (QQVGA/QVGA/HVGA/VGA), `jpeg_quality` (4–63; lower = better quality, bigger files), `xclk_hz`, `infer_every_n` (process every Nth camera frame), `metrics_interval_ms`, `score_thr`, `metrics_verbose` (per-frame FRAME_TIMING records, which have a cost of their own), `grid_every_n`, `thermal_warn_c`, `warmup_frames`, `wifi_mode`. These are tools for experiments, **not** recommended settings.

## 6. Data
- **Device METRICS** are aggregated per window (default 1 s): for each stage `n, min, mean, max, p95` (nearest-rank), plus counters since boot, memory (`*_min` = lowest ever since boot), die temperature, RSSI, Wi-Fi state, uptime and reset reason.
- **PC run folder** `runs/<time>_<device>/`: `hello.json`, `detections.jsonl`, `metrics.csv` (fixed columns), `events.jsonl`, `grid_snapshots.csv`, `host_stats.csv`, `stream.gmtp` (replayable with `pc/replay.py`), and `frames/` (only while recording).
- **Baseline folder** `baselines/<date>_<device>_<scene>/` from `scripts/collect_baseline.py`: `baseline.json` (provenance), `summary.csv` (one row per trial), and one run folder per trial.

## 7. Warm-up and repeatability
- Stream at least **2 minutes** before measuring, so the die temperature settles. `collect_baseline.py` does this (`--initial-warmup-s 120`) plus 15 s after every mode switch.
- Record the die temperature with every trial (it is in METRICS; `summary.csv` has start/end/max).
- Note the room temperature, the heatsink, the scene and the board MAC.
- Use the reference scenes: printed full-body silhouettes or a still photo on a monitor at a fixed distance under fixed lighting; or a looping walking video (licensed or self-recorded with consent) on a monitor. The camera goes on a tripod or clamp.
- The detector was trained on roughly eye-level views. Mount the camera at about 2–2.5 m with an oblique angle, people at least about ¼ of the frame height.
- Wi-Fi is shared airtime: other devices and people moving between the board and the router change `t_send`. Note the RSSI and the time of day.

## 8. Trial log template
Copy one block per trial into your notebook. The scripts fill most fields automatically (`baseline.json`, `summary.csv`).

```
Date / time:            Team:                 Trial id:
Question / hypothesis:
Board MAC:              Sensor PID:           Firmware (fw_version):
IDF / esp-dl / camera / model versions: (from hello.json)
Config changed from default: (key = value, or "none")
Mode:                   Repeat #:             Duration (s):     Warm-up (s):
Scene:                  Lighting:             Camera height/angle:
Room temp (°C):         Heatsink (y/n):       Power (port/hub):  RSSI (dBm):
Die temp start / end / max (°C):
Results (with units):   fps processed / sent / received:
                        t_decode / t_pre / t_infer / t_post / t_proc / t_send (mean, p95):
                        tx_dropped / frame_id gaps / missing pairs / seq gaps:
Run folder:
Observations (what surprised you?):
Competing explanations, and the test that would separate them:
```
