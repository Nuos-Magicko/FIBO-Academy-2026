# Research findings (Gate 0)

Date: 2026-10-15. Sources:
- Registry: https://components.espressif.com/components/espressif/pedestrian_detect, `/esp-dl`, `/esp32-camera`
- pedestrian_detect 0.3.2 README (registry)
- esp-dl example: https://github.com/espressif/esp-dl/tree/master/examples/pedestrian_detect (read at `17e9abb1`)

## Detector
- `PedestrianDetect` (pedestrian_detect 0.3.2) runs model `pico_s8_v1_s3`. The README gives these latencies on ESP32-S3: preprocess 9.2 ms, model 118.3 ms, postprocess 2.4 ms. These are **reference figures, not targets**.
- `run(img)` takes an `dl::image::img_t` of any size. Returned boxes `[x1,y1,x2,y2]` are in **input-image pixel coordinates**. Observed: the 640×480 still gave boxes within 640×480. For QVGA live frames, mapping to QVGA is therefore the identity. *To be confirmed against a saved frame (checklist item).*
- Pixel format: `DL_IMAGE_PIX_TYPE_RGB888`, as used in the official example.
- Model storage options: `CONFIG_PEDESTRIAN_DETECT_MODEL_IN_FLASH_RODATA` (chosen), `_FLASH_PARTITION` (needs a `pedestrian_det` partition), `_SDCARD`. With XIP-from-PSRAM on, the model is copied to PSRAM at load (`FbsLoader` warning, expected).
- `PedestrianDetect::run` returns only the total time. The `t_pre`/`t_infer`/`t_post` split needs either esp-dl's internal model/preprocessor classes or a wrapper. **Open item for Gate 2.**

## JPEG decode
- esp-dl 3.3.13 ships `dl::image::sw_decode_jpeg()` (software, built on esp_new_jpeg). Using it means one less dependency. The S3 has no hardware JPEG decoder.

## Camera
- esp32-camera 2.1.8 supports OV3660. XIAO ESP32S3 Sense pins: XCLK 10, SDA 40, SCL 39, D7..D0 = 48,11,12,14,16,18,17,15, VSYNC 38, HREF 47, PCLK 13; PWDN and RESET are not connected.
- The driver reports NO-EOI/NO-SOI/FB-OVF only via `ESP_LOG`. The firmware counts them by hooking `esp_log_set_vprintf`.

## Not yet checked
- `esp-who` reuse: skipped for now. The esp-dl example plus esp32-camera were enough for bring-up. Revisit only if Gate 2 needs its task pipeline.

## Board facts (unit #1, read 2026-10-15)
ESP32-S3 (QFN56) rev v0.2, 8 MB embedded PSRAM (octal), 8 MB flash (mfr 0xC8, dev 0x4017, quad eFuse), 40 MHz crystal, USB-Serial/JTAG on COM3, MAC `8c:bf:ea:8e:6d:d8`, camera PID **0x3660**.
