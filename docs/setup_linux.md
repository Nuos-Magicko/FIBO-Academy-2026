# Setup on Linux

> **NOT TESTED on Linux.** These steps follow the official ESP-IDF and Python procedures, but no one has run them on a Linux machine for this project yet. Record what happens in `test_report.md` the first time.

## 1. PC side
```bash
sudo apt install python3 python3-venv          # Debian/Ubuntu; Python 3.11+
python3 -m venv pc/.venv
pc/.venv/bin/pip install -r pc/requirements.txt
pc/.venv/bin/python -m pytest pc/tests -q          # expect: 27 passed
pc/.venv/bin/python pc/viewer.py --replay samples/synthetic_run
```
The viewer needs a desktop session. Over SSH, use `--snapshot out.png` to render headless.

## 2. Serial port access
The XIAO appears as `/dev/ttyACM0` (USB-Serial/JTAG, no driver needed). Add yourself to the serial group, then log out and back in:
```bash
sudo usermod -aG dialout $USER     # Arch/Fedora: uucp or dialout
```

## 3. Firmware build
```bash
sudo apt install git wget flex bison gperf python3-pip cmake ninja-build ccache libffi-dev libssl-dev dfu-util libusb-1.0-0
git clone -b v5.5.3 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf
~/esp/esp-idf/install.sh esp32s3
. ~/esp/esp-idf/export.sh
idf.py --version                    # must print v5.5.3

cp firmware/main/wifi_secrets.example.h firmware/main/wifi_secrets.h   # fill in a 2.4 GHz network
cd firmware
idf.py set-target esp32s3
idf.py build
idf.py merge-bin -o gmt_people_flow_merged.bin
```
After adding or deleting `wifi_secrets.h`, run `idf.py fullclean` (incremental builds don't notice). Build camp images from a committed tree, so the version doesn't end in `-dirty`.

## 4. Flash and check
```bash
chmod +x scripts/flash_all.sh
./scripts/flash_all.sh
```
Details and recovery: `flashing_and_recovery.md`.
