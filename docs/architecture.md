# Architecture

Target design: the handoff v2, §5 (vision task on core 1, net task on core 0, drop-oldest depth-2 bundle queue, preallocated JPEG pool, framed TCP protocol). This file records only what is decided or built.

## Current state (Gate 1 bring-up)
A single `app_main` loop in `firmware/main/main.cpp`:
`esp_camera_fb_get` → `dl::image::sw_decode_jpeg` (RGB888, PSRAM) → `esp_camera_fb_return` → `PedestrianDetect::run` → serial log.
The camera buffer is returned right after decode. The decoded image is freed every frame (per-frame malloc; Gate 2 replaces this with a reused buffer).

## Decisions so far
- JPEG decode: esp-dl's built-in `sw_decode_jpeg` (no extra dependency).
- Model in flash rodata (copied to PSRAM at load). No model partition needed.
- Partition table: nvs 24 K, phy 4 K, factory app 7 MB.
- FreeRTOS tick 1 kHz, so a 1-tick yield costs 1 ms instead of 10 ms.
- Detector boxes are in input-image pixels. Feeding QVGA frames gives QVGA coordinates directly.
