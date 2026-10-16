# Flashing and recovery (mentors)

All commands run from the repository root with the project venv (`pc\.venv\Scripts\python`). Only `pyserial` and `esptool` (both in `pc/requirements.txt`) are needed to flash. ESP-IDF is needed only to *build* an image.

## Flash and verify a board
1. Build the image once, from a committed tree (`setup_windows.md` §3): `firmware/build/gmt_people_flow_merged.bin`.
   It contains the Wi-Fi credentials from `wifi_secrets.h`. Keep it on mentor laptops and don't publish it.
2. Plug in **one** board: a direct laptop port or a **powered** hub. Find its COM port in Device Manager.
3. Run:
   ```powershell
   python pc/main.py verify-board --port COM3 --flash        # flash, then verify
   python pc/main.py verify-board --port COM3                # verify only, no flashing
   ```
   It takes about 2 minutes: flash, reboot, a 1000-frame capture test, the still-image self-test, then Wi-Fi. Repeat for the next board.
4. The board prints a line, which is also appended to `boards.csv`. Label the board with its MAC.
   ```
   PASS  COM3  mac=8c:bf:ea:8e:6d:d8  sensor=0x3660  psram=8MB  flash=8MB  fw=3a81649  ip=192.168.1.248
   ```
   A board **passes** only if the camera reads as `0x3660` (OV3660), it reports 8 MB PSRAM and 8 MB flash, the self-test finds 3 people in the still image, the capture test has 0 failures, and the firmware isn't `-dirty`.

Seeed shipped both OV2640 and OV3660 during a transition period. An OV2640 board (reported as `sensor=0x0026`) fails on purpose; set it aside rather than mixing it into experiments.

## Recovery

| Symptom | Fix |
|---|---|
| No COM port appears | Try another cable (many are charge-only) and a direct port. Still nothing: hold **BOOT** (the small button by the USB-C connector), plug in, release; this forces download mode. Then flash again. |
| esptool "Failed to connect" / "No serial data received" | Same as above (BOOT + replug). Close anything that holds the port (`idf.py monitor`, `pc/main.py serial-log`, `pc/main.py soak`). |
| Board flashed but unreachable on Wi-Fi | The serial log shows why: `python pc/main.py serial-log COM3 120 boot.log`, then look for `wifi_disconnected reason=201` (network not found: wrong name, or a 5 GHz network) or `reason=202`, `15` or `204` (usually a wrong password). |
| A wrong network name or password was **saved** from the viewer/test client | After 8 failed tries (about 2 minutes) the board starts its own network `GMT-PF-xxxx` (password `gmtcamp2026`, 192.168.4.1). Either connect a PC to that network and send `{"cmd":"set","values":{"sta_ssid":"…","sta_pass":"…"}}` + `save` + `reboot`, or erase the saved settings over USB (next row). |
| Reset all saved settings to the image's defaults | `python -m esptool --chip esp32s3 --port COM3 erase_region 0x9000 0x6000` (erases only the NVS settings partition; verified on hardware). |
| Anything else odd: start over | `python -m esptool --chip esp32s3 --port COM3 erase_flash`, then flash again. |
| `camera_init_failed` event / `STOP: no camera` | Unplug, reseat the Sense expansion board and the camera ribbon, replug. |
| `capture_timeout` or `no_eoi` counters rising | Lower the camera clock: `{"cmd":"set","values":{"xclk_hz":10000000}}` (documented fallback). Also check the ribbon. |
| Random resets, `reset_reason=BROWNOUT`, camera init failures under load | Power: use a direct port or a powered hub, a shorter/better cable, one board per port. |
| `thermal_warning` event (die ≥ 80 °C by default) | Check the airflow; PLA enclosures can deform. The firmware warns but does **not** throttle itself. |
| `sensor_mismatch` event | Not an OV3660: set the board aside (see above). |

## Changing settings without rebuilding
Any client can send COMMANDs (see `protocol.md`). For example, with the test client:
```bash
python pc/main.py check 192.168.1.248 --seconds 3 --cmd '{"cmd":"set","values":{"xclk_hz":10000000}}' --cmd '{"cmd":"save"}'
```
`save` keeps the change across reboots. Wi-Fi keys need `save` then `reboot`. The Wi-Fi password travels in plain text over the local network; this is acceptable for a closed camp network only.

## Manual flashing (without the scripts)
```bash
python -m esptool --chip esp32s3 --port COM3 -b 460800 write_flash 0x0 firmware/build/gmt_people_flow_merged.bin
```
