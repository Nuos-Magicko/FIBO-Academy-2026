# GMTP wire protocol v1

The device is a TCP server on port **5000** and serves one client at a time. Records are sent over TCP only; logs go to USB serial and never to the data stream.
Reference parser: `pc/connection/protocol.py` (tests: `pc/tests/test_protocol.py`). Device side: `firmware/main/network/protocol.cpp`.

## Record framing (all little-endian)

| Field | Size | Notes |
|---|---|---|
| magic | 4 | `"GMTP"` |
| version | 1 | `1` |
| type | 1 | see below |
| flags | 2 | `0` (reserved) |
| seq | 4 | per connection, starts at 0, +1 per record; a gap means records were lost |
| frame_id | 4 | ID of the processed frame, `0` if not frame-related. Increases by 1 per processed frame, so gaps = frames dropped by drop-oldest |
| t_dev_us | 8 | `esp_timer_get_time()` when the record was sent |
| len | 4 | payload length, ≤ 65536 (PC → device: ≤ 1024) |
| payload | len | |
| crc32 | 4 | zlib CRC-32 over header + payload (device: chained `esp_rom_crc32_le(0, …)`, verified equal on hardware) |

Parsers must resync on `magic` after corruption, reject oversize `len`, and drop records whose CRC fails. Only `json.loads` is used for JSON payloads (no pickle).

## Record types

| Type | Name | Direction | Payload |
|---|---|---|---|
| 1 | HELLO | dev → PC | JSON: `proto_version, device_id, mac, fw_version` (git describe), `fw_build, idf, esp_dl, esp32_camera, pedestrian_detect, model, chip, chip_rev, flash_bytes, psram_bytes, sensor_pid, grid_cols, grid_rows, box_coords, reset_reason, wifi, ip, config{…}`. Sent on connect, after every accepted `set`, and on `get_hello`. `config` never includes the Wi-Fi password. |
| 2 | JPEG_FRAME | dev → PC | `w u16, h u16, quality u8, pad[3], t_capture_us i64` (16 B), then the JPEG bytes. `full_stream` only |
| 3 | DETECTIONS | dev → PC | `space u8 (0 = frame_px), count u8, w u16, h u16, pad u16` (8 B), then `count` × `x1 i16, y1 i16, x2 i16, y2 i16, score f32, cls u8 (0 = person), pad[3]` (16 B each). Boxes are in pixels of the frame they came from (`w`×`h`), clamped to the frame |
| 4 | PRESENCE_GRID | dev → PC | `cols u16, rows u16, frames u32, elapsed_us i64, frame_w u16, frame_h u16` (20 B), then `cols×rows` u32 counts, row-major. Sent every `grid_every_n` sent bundles (streaming modes) and on `get_grid`/`reset_grid` |
| 5 | METRICS | dev → PC | JSON, one per `metrics_interval_ms` window (see below) |
| 6 | EVENT | dev → PC | JSON `{severity: info/warn/error, code, text, t_event_us}` |
| 7 | COMMAND | PC → dev | JSON (see below) |
| 8 | PONG | dev → PC | JSON `{t_host_us, t_dev_us}` |
| 9 | FRAME_TIMING | dev → PC | JSON per bundle when `metrics_verbose` is true: `frame_id` + the per-frame stage times known before hand-off (`t_enqueue` and `t_proc` are not included; METRICS has them) |

### Bundles and pairing
In `full_stream`, a processed frame is sent as JPEG_FRAME then DETECTIONS with the **same frame_id**, back to back (then FRAME_TIMING if verbose). Only inferred frames are streamed, so overlays are aligned by construction. A client must never draw DETECTIONS for frame n+1 on frame n. `pc/main.py check` counts any such mismatch as a failure.
In `metadata_stream` and `inference_only`, DETECTIONS are sent without JPEG_FRAME. In `inference_only`, `w×h` is the 640×480 still.

### METRICS JSON
```
window_us, fps_processed, fps_sent, bytes_per_s, dropped_in_window,
stages: { t_wait_frame|t_decode|t_pre|t_infer|t_post|t_grid|t_copy|t_enqueue|t_proc|t_send:
          {n, min, mean, max, p95 [, overflow]} }   (µs; only stages with samples in the window)
counters: { cap_ok, cap_timeout, no_eoi, no_soi, fb_ovf, decode_fail, oversize, frames_processed,
            cadence_skipped, still_runs, bundles_sent, tx_dropped, no_client_discards, events_dropped,
            clients, cmd_ok, cmd_err, rx_crc_errors, rx_oversize, wifi_disconnects, bytes_sent,
            ready_queue_depth }          (since boot)
mem: { int_free, int_min, ps_free, ps_min }   (bytes; *_min = minimum ever since boot)
temp_c, rssi (0 if not station-connected), wifi (sta_connecting|sta_connected|ap), wifi_reconnects,
uptime_s, reset_reason, mode
```
p95 is nearest-rank (`ceil(0.95·n)`-th sorted sample). At most 512 samples per stage per window; extras are counted in `overflow`.

### COMMAND JSON
| Command | Effect |
|---|---|
| `{"cmd":"ping","t_host_us":N}` | PONG echoing `N` plus device time. Clock offset ≈ `t_dev − (t_host + rtt/2)`, uncertainty ±rtt/2 |
| `{"cmd":"set","values":{…}}` | Validates **all** keys first; on any error nothing changes and `cmd_error` explains why. On success: `cmd_ok` + a new HELLO. Keys: `mode, frame_size (QQVGA/QVGA/HVGA/VGA), jpeg_quality (4–63), xclk_hz, warmup_frames, infer_every_n, infer_only_runs, metrics_interval_ms, grid_every_n, score_thr, thermal_warn_c, metrics_verbose, wifi_mode (sta/ap), sta_ssid, sta_pass`. Camera keys restart the camera (plus warm-up); a frame-size change also resets the grid. Wi-Fi keys apply after `save` + `reboot` |
| `{"cmd":"save"}` | Writes the current settings to NVS (loaded at boot) |
| `{"cmd":"reset_grid"}` / `{"cmd":"get_grid"}` | Clear / send PRESENCE_GRID |
| `{"cmd":"get_hello"}` | Send HELLO |
| `{"cmd":"reboot"}` | Restart the device |

### Event codes
`capture_test, self_test, sensor_mismatch, camera_init_failed, camera_reconfigured, capture_timeout` (1st and every 100th), `mode_changed, grid_reset, inference_only_done, thermal_warning, thermal_ok, queue_drop_burst` (≥ 10 drops in a window), `wifi_connected, wifi_disconnected, wifi_fallback_ap, client_connected, client_lost, cmd_ok, cmd_error, brownout_reset, net_listen_failed`.
Events are queued (depth 16, oldest dropped and counted in `events_dropped`) while no client is connected, so a new client receives the most recent ones, including boot events.

## Backpressure
- `full_stream`: ready queue depth 2. When it's full, the **oldest** bundle is dropped (`tx_dropped`). Inference never waits on the network.
- `metadata_stream` / `inference_only`: the vision task waits up to 1 s for queue space, then drops the oldest.
- No client: bundles are discarded (`no_client_discards`), events are kept.
- Socket send timeout is 5 s; a longer stall drops the client, which can then reconnect.
