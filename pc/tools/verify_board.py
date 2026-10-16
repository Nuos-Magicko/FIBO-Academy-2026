"""Flash (optional) and verify camp boards: MAC, chip, flash, PSRAM, camera PID, firmware hash, self-tests.

  python pc/main.py verify-board --port COM3                 verify one board (reboots it)
  python pc/main.py verify-board --all                       every ESP32-S3 USB port (VID 0x303A)
  python pc/main.py verify-board --all --flash               flash the merged image first, then verify
  python pc/main.py verify-board --all --flash --image path/to/gmt_people_flow_merged.bin

Default image: firmware/build/gmt_people_flow_merged.bin (make it with `idf.py merge-bin -o
gmt_people_flow_merged.bin` in firmware/). An image built with wifi_secrets.h contains the Wi-Fi
password: keep it on mentor laptops, don't publish it.

Boards are handled one at a time: flashing several boards from one USB hub at once can brown it out.
Appends one row per board to boards.csv (or --csv) and exits 1 if any board fails a check.
Needs pyserial + esptool (pc/requirements.txt). Run with the pc/.venv Python.
"""
import argparse
import csv
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import serial
from serial.tools import list_ports

from utils.paths import FIRMWARE_BUILD_DIR, REPO_ROOT
from utils.usb_serial import BAUD, DEVICE_IP, RESET_REASON, clean_line, reset_board, split_lines

DEFAULT_IMAGE = FIRMWARE_BUILD_DIR / "gmt_people_flow_merged.bin"
ESPRESSIF_USB_VID = 0x303A
FLASH_BAUD = "460800"
EXPECTED = {"sensor_pid": "0x3660", "psram_mb": 8, "flash_mb": 8, "self_test_boxes": 3, "capture_fail": 0}
COLUMNS = ["time", "port", "mac", "chip_rev", "flash_mb", "psram_mb", "sensor_pid", "fw_version", "idf",
           "reset_reason", "capture_test", "capture_fail", "self_test_boxes", "ip", "flashed", "result", "problems"]
CHIP_FACT_PATTERNS = {
    "mac": (re.compile(r"MAC: ([0-9a-f:]{17})"), str),
    "chip_rev": (re.compile(r"\(revision (v[\d.]+)\)"), str),
    "flash_mb": (re.compile(r"Detected flash size: (\d+)MB"), int),
    "psram_mb": (re.compile(r"Embedded PSRAM (\d+)MB"), int),
}
BOOT_LOG_PATTERNS = {
    "fw_version": re.compile(r"App version:\s+(\S+)"),
    "idf": re.compile(r"app_init: ESP-IDF:\s+(\S+)"),
    "sensor_pid": re.compile(r"sensor PID=(0x[0-9a-f]+)"),
    "reset_reason": RESET_REASON,
    "self_test_boxes": re.compile(r"EVENT self_test still \d+x\d+ boxes=(\d+)"),
    "ip": DEVICE_IP,
}
CAPTURE_TEST = re.compile(r"EVENT capture_test (n=\d+ ok=\d+ fail=(\d+).*)")
BOOT_FACTS_NEEDED = ("self_test_boxes", "capture_fail", "ip")


def esptool(port, *args, timeout=180):
    """Run esptool for one port; returns (exit code, combined output)."""
    command = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout + result.stderr


def chip_facts(port):
    """MAC, revision, flash and PSRAM size as reported by esptool flash_id."""
    exit_code, output = esptool(port, "flash_id")
    facts = {}
    for key, (pattern, convert) in CHIP_FACT_PATTERNS.items():
        if match := pattern.search(output):
            facts[key] = convert(match.group(1))
    return exit_code, facts


def parse_boot_line(line, facts):
    for key, pattern in BOOT_LOG_PATTERNS.items():
        if (match := pattern.search(line)) and key not in facts:
            facts[key] = int(match.group(1)) if key == "self_test_boxes" else match.group(1)
    if match := CAPTURE_TEST.search(line):
        facts["capture_test"], facts["capture_fail"] = match.group(1), int(match.group(2))


def read_boot(port_name, seconds):
    """Reset the board and parse its boot log until the capture test, self-test and IP have appeared."""
    facts, log = {}, []
    with serial.Serial(port_name, BAUD, timeout=0.5) as port:
        reset_board(port)
        deadline, pending = time.time() + seconds, b""
        while time.time() < deadline and not all(key in facts for key in BOOT_FACTS_NEEDED):
            lines, pending = split_lines(pending, port.read(4096))
            for raw in lines:
                line = clean_line(raw)
                log.append(line)
                parse_boot_line(line, facts)
    return facts, log


def problems_with(facts):
    problems = [f"{key}={facts.get(key)!r} (want {want!r})" for key, want in EXPECTED.items() if facts.get(key) != want]
    if str(facts.get("fw_version", "")).endswith("-dirty"):
        problems.append("fw built from uncommitted changes (-dirty)")
    return problems


def flash(port, image):
    """Write the merged image at 0x0; returns an error message, or None when the hash verified."""
    exit_code, output = esptool(port, "-b", FLASH_BAUD, "write_flash", "0x0", str(image), timeout=300)
    if exit_code != 0 or "Hash of data verified" not in output:
        return "flash failed: " + output.strip().splitlines()[-1]
    return None


def verify(port, args):
    row = {"time": datetime.now().isoformat(timespec="seconds"), "port": port, "flashed": "no"}
    exit_code, facts = chip_facts(port)
    if exit_code != 0:
        row.update(result="FAIL", problems="esptool could not talk to the chip (try BOOT button + replug)")
        return row
    row.update(facts)
    if args.flash:
        error = flash(port, args.image)
        if error:
            row.update(result="FAIL", flashed="error", problems=error)
            return row
        row["flashed"] = "yes (hash verified)"
    boot_facts, log = read_boot(port, args.wait)
    row.update(boot_facts)
    problems = problems_with(row)
    row.update(result="PASS" if not problems else "FAIL", problems="; ".join(problems))
    if problems and args.verbose:
        print("\n".join(log[-40:]))
    return row


def board_line(row):
    return (f"{row['result']}  {row['port']}  mac={row.get('mac')}  sensor={row.get('sensor_pid')}  "
            f"psram={row.get('psram_mb')}MB  flash={row.get('flash_mb')}MB  fw={row.get('fw_version')}  "
            f"ip={row.get('ip')}  {row.get('problems', '')}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--port", action="append", help="serial port (repeatable)")
    target.add_argument("--all", action="store_true", help="every Espressif USB port (VID 0x303A)")
    parser.add_argument("--flash", action="store_true")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--wait", type=float, default=90, help="seconds to wait for boot self-tests")
    parser.add_argument("--csv", type=Path, default=REPO_ROOT / "boards.csv")
    parser.add_argument("--verbose", action="store_true", help="print the boot log tail for failing boards")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.flash and not args.image.exists():
        sys.exit(f"image not found: {args.image}")
    ports = args.port or sorted(p.device for p in list_ports.comports() if p.vid == ESPRESSIF_USB_VID)
    if not ports:
        sys.exit("no ESP32-S3 USB ports found (check the cable; hold BOOT while plugging in if the board is stuck)")
    write_header = not args.csv.exists()
    rows = []
    with open(args.csv, "a", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for port in ports:
            print(f"--- {port} ...", flush=True)
            row = verify(port, args)
            writer.writerow(row)
            csv_file.flush()
            rows.append(row)
            print(board_line(row), flush=True)
    failed = [row for row in rows if row["result"] != "PASS"]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} boards passed; results appended to {args.csv}")
    return 1 if failed else 0

