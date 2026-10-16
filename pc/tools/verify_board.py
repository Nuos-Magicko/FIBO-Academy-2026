"""Flash (optional) and verify one camp board.

Checks MAC, chip, flash, PSRAM, camera PID, firmware hash and self-tests.

Verify the board on COM3 (it reboots):
  python pc/main.py verify-board --port COM3
Flash the merged image first, then verify:
  python pc/main.py verify-board --port COM3 --flash
  python pc/main.py verify-board --port COM3 --flash \
      --image path/to/gmt_people_flow_merged.bin

Default image: firmware/build/gmt_people_flow_merged.bin (make it with `idf.py
merge-bin -o gmt_people_flow_merged.bin` in firmware/). An image built with
wifi_secrets.h contains the Wi-Fi password: keep it on mentor laptops, don't
publish it.

Flash boards one at a time: flashing several boards from one USB hub at once
can brown it out. Appends one row to boards.csv (or --csv) and exits 1 if the
board fails a check. Needs pyserial + esptool
(pc/requirements.txt). Run with the pc/.venv Python.
"""

import argparse
import csv
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import serial

from utils.paths import FIRMWARE_BUILD_DIR, REPO_ROOT
from utils.usb_serial import (
    BAUD,
    DEVICE_IP,
    RESET_REASON,
    clean_line,
    reset_board,
    split_lines,
)

DEFAULT_IMAGE = FIRMWARE_BUILD_DIR / "gmt_people_flow_merged.bin"
FLASH_BAUD = "460800"
EXPECTED = {
    "sensor_pid": "0x3660",
    "psram_mb": 8,
    "flash_mb": 8,
    "self_test_boxes": 3,
    "capture_fail": 0,
}
COLUMNS = [
    "time",
    "port",
    "mac",
    "chip_rev",
    "flash_mb",
    "psram_mb",
    "sensor_pid",
    "fw_version",
    "idf",
    "reset_reason",
    "capture_test",
    "capture_fail",
    "self_test_boxes",
    "ip",
    "flashed",
    "result",
    "problems",
]
MAC_PATTERN = re.compile(r"MAC: ([0-9a-f:]{17})")
CHIP_REVISION_PATTERN = re.compile(r"\(revision (v[\d.]+)\)")
FLASH_SIZE_PATTERN = re.compile(r"Detected flash size: (\d+)MB")
PSRAM_SIZE_PATTERN = re.compile(r"Embedded PSRAM (\d+)MB")
BOOT_LOG_PATTERNS = {
    "fw_version": re.compile(r"App version:\s+(\S+)"),
    "idf": re.compile(r"app_init: ESP-IDF:\s+(\S+)"),
    "sensor_pid": re.compile(r"sensor PID=(0x[0-9a-f]+)"),
    "reset_reason": RESET_REASON,
    "self_test_boxes": re.compile(
        r"EVENT self_test still \d+x\d+ boxes=(\d+)"
    ),
    "ip": DEVICE_IP,
}
CAPTURE_TEST = re.compile(r"EVENT capture_test (n=\d+ ok=\d+ fail=(\d+).*)")
BOOT_FACTS_NEEDED = ("self_test_boxes", "capture_fail", "ip")


def esptool(
    port: str, arguments: list[str], timeout: int = 180
) -> tuple[int, str]:
    """Run esptool for one port with a list of arguments; returns (exit code,
    combined output)."""
    command = [
        sys.executable,
        "-m",
        "esptool",
        "--chip",
        "esp32s3",
        "--port",
        port,
    ] + arguments
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout + result.stderr


def chip_facts(port: str) -> tuple[int, dict[str, Any]]:
    """MAC, revision, flash and PSRAM size as reported by esptool flash_id."""
    exit_code, output = esptool(port, ["flash_id"])
    facts: dict[str, Any] = {}
    mac = first_match(MAC_PATTERN, output)
    if mac is not None:
        facts["mac"] = mac
    chip_revision = first_match(CHIP_REVISION_PATTERN, output)
    if chip_revision is not None:
        facts["chip_rev"] = chip_revision
    flash_size = first_match(FLASH_SIZE_PATTERN, output)
    if flash_size is not None:
        facts["flash_mb"] = int(flash_size)
    psram_size = first_match(PSRAM_SIZE_PATTERN, output)
    if psram_size is not None:
        facts["psram_mb"] = int(psram_size)
    return exit_code, facts


def first_match(pattern: re.Pattern[str], text: str) -> str | None:
    """The first group of the pattern's first match in text, or None."""
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1)


def parse_boot_line(line: str, facts: dict[str, Any]) -> None:
    for key, pattern in BOOT_LOG_PATTERNS.items():
        if key in facts:
            continue
        value = first_match(pattern, line)
        if value is None:
            continue
        if key == "self_test_boxes":
            facts[key] = int(value)
        else:
            facts[key] = value
    capture_match = CAPTURE_TEST.search(line)
    if capture_match is not None:
        facts["capture_test"] = capture_match.group(1)
        facts["capture_fail"] = int(capture_match.group(2))


def has_all_boot_facts(facts: dict[str, Any]) -> bool:
    for key in BOOT_FACTS_NEEDED:
        if key not in facts:
            return False
    return True


def read_boot(
    port_name: str, seconds: float
) -> tuple[dict[str, Any], list[str]]:
    """Reset the board and parse its boot log until the capture test, self-test
    and IP have appeared."""
    facts: dict[str, Any] = {}
    log: list[str] = []
    with serial.Serial(port_name, BAUD, timeout=0.5) as port:
        reset_board(port)
        deadline = time.time() + seconds
        pending = b""
        while time.time() < deadline and not has_all_boot_facts(facts):
            lines, pending = split_lines(pending, port.read(4096))
            for raw in lines:
                line = clean_line(raw)
                log.append(line)
                parse_boot_line(line, facts)
    return facts, log


def problems_with(facts: dict[str, Any]) -> list[str]:
    problems = []
    for key, want in EXPECTED.items():
        if facts.get(key) != want:
            problems.append(f"{key}={facts.get(key)!r} (want {want!r})")
    if str(facts.get("fw_version", "")).endswith("-dirty"):
        problems.append("fw built from uncommitted changes (-dirty)")
    return problems


def flash(port: str, image: Path) -> str | None:
    """Write the merged image at 0x0; returns an error message, or None when
    the hash verified."""
    exit_code, output = esptool(
        port, ["-b", FLASH_BAUD, "write_flash", "0x0", str(image)], timeout=300
    )
    if exit_code != 0 or "Hash of data verified" not in output:
        return "flash failed: " + output.strip().splitlines()[-1]
    return None


def verify(port: str, args: argparse.Namespace) -> dict[str, Any]:
    row = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "port": port,
        "flashed": "no",
    }
    exit_code, facts = chip_facts(port)
    if exit_code != 0:
        row["result"] = "FAIL"
        row["problems"] = (
            "esptool could not talk to the chip (try BOOT button + replug)"
        )
        return row
    row.update(facts)
    if args.flash:
        error = flash(port, args.image)
        if error is not None:
            row["result"] = "FAIL"
            row["flashed"] = "error"
            row["problems"] = error
            return row
        row["flashed"] = "yes (hash verified)"
    boot_facts, log = read_boot(port, args.wait)
    row.update(boot_facts)
    problems = problems_with(row)
    if problems:
        row["result"] = "FAIL"
    else:
        row["result"] = "PASS"
    row["problems"] = "; ".join(problems)
    if problems and args.verbose:
        print("\n".join(log[-40:]))
    return row


def board_line(row: dict[str, Any]) -> str:
    return (
        f"{row['result']}  {row['port']}  mac={row.get('mac')}"
        f"  sensor={row.get('sensor_pid')}  psram={row.get('psram_mb')}MB"
        f"  flash={row.get('flash_mb')}MB  fw={row.get('fw_version')}"
        f"  ip={row.get('ip')}  {row.get('problems', '')}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--port", required=True, help="the board's serial port, e.g. COM3"
    )
    parser.add_argument("--flash", action="store_true")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument(
        "--wait",
        type=float,
        default=90,
        help="seconds to wait for boot self-tests",
    )
    parser.add_argument("--csv", type=Path, default=REPO_ROOT / "boards.csv")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print the boot log tail for failing boards",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.flash and not args.image.exists():
        sys.exit(f"image not found: {args.image}")
    write_header = not args.csv.exists()
    print(f"--- {args.port} ...", flush=True)
    row = verify(args.port, args)
    with open(args.csv, "a", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    print(board_line(row), flush=True)
    print(f"results appended to {args.csv}")
    if row["result"] != "PASS":
        return 1
    return 0
