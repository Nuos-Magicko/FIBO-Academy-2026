# Setup on Windows (verified on Windows 11, 2026-10-15)

## 1. PC side (everyone)
1. Install Python 3.11 or newer from python.org (3.12.4 verified).
2. In the repository folder:
   ```bash
   python -m venv pc\.venv
   pc\.venv\Scripts\pip install -r pc\requirements.txt
   pc\.venv\Scripts\python -m pytest pc\tests -q
   ```
   The last line should report `36 passed`.
3. Check the viewer without hardware:
   ```bash
   pc\.venv\Scripts\python pc\main.py viewer --replay samples\synthetic_run
   ```

## 2. Board connection
- Plug the XIAO into a direct laptop USB port or a powered hub. No driver is needed: Device Manager shows **USB Serial Device (COMx)** and **USB JTAG/serial debug unit**. Bluetooth "Standard Serial over Bluetooth link" ports are not the board.
- The board only joins **2.4 GHz** networks. During testing, the PC was on the same 2.4 GHz network as the board. A PC on the router's 5 GHz network may also reach the board if the router bridges the two bands, but that is **not tested**.

## 3. Firmware build (mentors / anyone changing firmware)
1. Install ESP-IDF **v5.5.3** with the Espressif Installation Manager (EIM).
   - Check the version with `idf.py --version`; don't trust the install folder name. On the reference PC, the folder was named `v5.5.1` but contained v5.5.3.
   - The project pins `idf: ==5.5.3` and refuses other versions.
2. Wi-Fi credentials: copy `firmware\main\config\wifi_secrets.example.h` to `firmware\main\config\wifi_secrets.h` and fill in a 2.4 GHz network.
   - The copy is git-ignored. **Don't** edit the example file: it is tracked.
   - Without the file, the board starts its own network `GMT-PF-xxxx` (password `gmtcamp2026`, board at 192.168.4.1).
   - Credentials can also be changed later without rebuilding (see `flashing_and_recovery.md`).
3. Build (PowerShell):
   ```powershell
   . $env:IDF_PATH\export.ps1
   cd firmware
   idf.py set-target esp32s3      # first time only
   idf.py build
   idf.py merge-bin -o gmt_people_flow_merged.bin   # single image for verify-board --flash
   ```
   The first build downloads the pinned components (`dependencies.lock`) and takes a few minutes.
4. After **adding or deleting** `wifi_secrets.h`, an incremental build won't notice. Run `idf.py fullclean`, or touch `main\config\settings.cpp`, before building.
5. Build from a **committed** tree for camp boards. Otherwise the firmware version ends in `-dirty`, and `pc/main.py verify-board` fails it on purpose.

## 4. Flash and check
```powershell
pc\.venv\Scripts\python pc\main.py verify-board --port COM3 --flash   # one board: flash, then verify
```
See `flashing_and_recovery.md` for single boards, manual esptool commands and fixes.

## 5. Serial log (optional)
```powershell
idf.py -p COM3 monitor          # Ctrl+] to exit; resets the board
```
Or, without ESP-IDF: `pc\.venv\Scripts\python pc\main.py serial-log COM3 60 boot.log`.
