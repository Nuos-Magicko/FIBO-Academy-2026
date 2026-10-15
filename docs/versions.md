# Pinned versions

Verified 2026-10-15 against the Espressif Component Registry. Never track `master`.
The source of truth is `firmware/main/idf_component.yml` (direct pins) plus `firmware/dependencies.lock` (full tree).

| Component | Version | License | Notes |
|---|---|---|---|
| ESP-IDF | **v5.5.3** | Apache-2.0 | Installed at `C:\Users\thefo\esp\v5.5.1\esp-idf`. The folder says 5.5.1, but `idf.py --version` reports v5.5.3. |
| Toolchain | xtensa-esp-elf esp-14.2.0_20251107 | GPL (toolchain) | Ships with IDF 5.5.3 |
| espressif/pedestrian_detect | **0.3.2** (2026-05-12) | MIT | Model `pico_s8_v1_s3`, 224×224×3; requires esp-dl ~3.3.0 |
| espressif/esp-dl | **3.3.13** (2026-09-30) | MIT | Includes `dl::image::sw_decode_jpeg` (wraps esp_new_jpeg) |
| espressif/esp32-camera | **2.1.8** (2026-09-25) | Custom (see registry) | Requires IDF ≥ 5.1 |
| espressif/esp_new_jpeg | 1.0.2 (transitive) | Custom | JPEG decoder used by esp-dl |
| espressif/esp_jpeg | 1.3.1 (transitive) | Apache-2.0 | Pulled in by esp32-camera |
| espressif/dl_fft | 0.8.0 (transitive) | — | Pulled in by esp-dl |
| esptool | 4.12.0 | GPL-2.0 | From the IDF Python env |
| Host Python | 3.12.4 | — | IDF env `idf5.5_py3.12_env` |

Test image `firmware/main/pedestrian.jpg` (640×480): from espressif/esp-dl
`examples/pedestrian_detect/main/pedestrian.jpg`, last changed in commit `9b2347b5`
(2024-08-30), fetched at master `17e9abb1`. esp-dl is MIT-licensed.

PC-side versions (`pc/requirements.txt`) get pinned in Gate 3.
