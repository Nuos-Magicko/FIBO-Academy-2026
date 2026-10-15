"""Flash (optional) and verify camp boards: MAC, chip, flash, PSRAM, camera PID, firmware hash, self-tests.

  python scripts/verify_board.py --port COM3                 # verify one board (reboots it)
  python scripts/verify_board.py --all                       # every ESP32-S3 USB port (VID 0x303A)
  python scripts/verify_board.py --all --flash               # flash the merged image first, then verify
  python scripts/verify_board.py --all --flash --image path/to/gmt_people_flow_merged.bin

Default image: firmware/build/gmt_people_flow_merged.bin (make it with `idf.py merge-bin -o
gmt_people_flow_merged.bin` in firmware/). An image built with wifi_secrets.h contains the Wi-Fi
password: keep it on mentor laptops, don't publish it.

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

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = ROOT / "firmware" / "build" / "gmt_people_flow_merged.bin"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
EXPECT = {"sensor_pid": "0x3660", "psram_mb": 8, "flash_mb": 8, "self_test_boxes": 3, "capture_fail": 0}
COLUMNS = ["time", "port", "mac", "chip_rev", "flash_mb", "psram_mb", "sensor_pid", "fw_version", "idf",
           "reset_reason", "capture_test", "capture_fail", "self_test_boxes", "ip", "flashed", "result", "problems"]


def esptool(port, *args, timeout=180):
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port, *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def chip_facts(port):
    rc, out = esptool(port, "flash_id")
    facts = {}
    if m := re.search(r"MAC: ([0-9a-f:]{17})", out):
        facts["mac"] = m.group(1)
    if m := re.search(r"\(revision (v[\d.]+)\)", out):
        facts["chip_rev"] = m.group(1)
    if m := re.search(r"Detected flash size: (\d+)MB", out):
        facts["flash_mb"] = int(m.group(1))
    if m := re.search(r"Embedded PSRAM (\d+)MB", out):
        facts["psram_mb"] = int(m.group(1))
    return rc, facts, out


def read_boot(port, seconds):
    """Reset via RTS and parse the boot log until the self-test and capture test have reported."""
    facts, log = {}, []
    with serial.Serial(port, 115200, timeout=0.5) as s:
        s.dtr = False
        s.rts = True
        time.sleep(0.1)
        s.rts = False
        end, buf = time.time() + seconds, b""
        while time.time() < end:
            buf += s.read(4096)
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = ANSI.sub("", raw.decode("utf8", "replace")).strip()
                log.append(line)
                if m := re.search(r"App version:\s+(\S+)", line):
                    facts["fw_version"] = m.group(1)
                elif m := re.search(r"app_init: ESP-IDF:\s+(\S+)", line):
                    facts["idf"] = m.group(1)
                elif m := re.search(r"sensor PID=(0x[0-9a-f]+)", line):
                    facts.setdefault("sensor_pid", m.group(1))
                elif m := re.search(r"BOARD reset_reason=(\w+)", line):
                    facts["reset_reason"] = m.group(1)
                elif m := re.search(r"EVENT capture_test (n=\d+ ok=\d+ fail=(\d+).*)", line):
                    facts["capture_test"], facts["capture_fail"] = m.group(1), int(m.group(2))
                elif m := re.search(r"EVENT self_test still \d+x\d+ boxes=(\d+)", line):
                    facts["self_test_boxes"] = int(m.group(1))
                elif m := re.search(r"NET mode=\w+ (?:ssid=.* )?ip=(\d+\.\d+\.\d+\.\d+)", line):
                    facts["ip"] = m.group(1)
            if "self_test_boxes" in facts and "capture_fail" in facts and "ip" in facts:
                break
    return facts, log


def check(facts):
    problems = []
    for k, want in EXPECT.items():
        if facts.get(k) != want:
            problems.append(f"{k}={facts.get(k)!r} (want {want!r})")
    if str(facts.get("fw_version", "")).endswith("-dirty"):
        problems.append("fw built from uncommitted changes (-dirty)")
    return problems


def verify(port, a):
    row = {"time": datetime.now().isoformat(timespec="seconds"), "port": port, "flashed": "no"}
    rc, facts, out = chip_facts(port)
    if rc != 0:
        row.update(result="FAIL", problems="esptool could not talk to the chip (try BOOT button + replug)")
        return row
    row.update(facts)
    if a.flash:
        rc, out = esptool(port, "-b", "460800", "write_flash", "0x0", str(a.image), timeout=300)
        if rc != 0 or "Hash of data verified" not in out:
            row.update(result="FAIL", flashed="error", problems="flash failed: " + out.strip().splitlines()[-1])
            return row
        row["flashed"] = "yes (hash verified)"
    boot, log = read_boot(port, a.wait)
    row.update(boot)
    problems = check(row)
    row.update(result="PASS" if not problems else "FAIL", problems="; ".join(problems))
    if problems and a.verbose:
        print("\n".join(log[-40:]))
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--port", action="append", help="serial port (repeatable)")
    g.add_argument("--all", action="store_true", help="every Espressif USB port (VID 0x303A)")
    ap.add_argument("--flash", action="store_true")
    ap.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    ap.add_argument("--wait", type=float, default=90, help="seconds to wait for boot self-tests")
    ap.add_argument("--csv", type=Path, default=ROOT / "boards.csv")
    ap.add_argument("--verbose", action="store_true", help="print the boot log tail for failing boards")
    a = ap.parse_args()
    if a.flash and not a.image.exists():
        sys.exit(f"image not found: {a.image}")
    ports = a.port or sorted(p.device for p in list_ports.comports() if p.vid == 0x303A)
    if not ports:
        sys.exit("no ESP32-S3 USB ports found (check the cable; hold BOOT while plugging in if the board is stuck)")

    new = not a.csv.exists()
    rows = []
    with open(a.csv, "a", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, extrasaction="ignore")
        if new:
            w.writeheader()
        for port in ports:  # one at a time: flashing several boards from one USB hub at once browns out hubs
            print(f"--- {port} ...", flush=True)
            row = verify(port, a)
            w.writerow(row)
            f.flush()
            rows.append(row)
            print(f"{row['result']}  {port}  mac={row.get('mac')}  sensor={row.get('sensor_pid')}  "
                  f"psram={row.get('psram_mb')}MB  flash={row.get('flash_mb')}MB  fw={row.get('fw_version')}  "
                  f"ip={row.get('ip')}  {row.get('problems', '')}", flush=True)
    bad = [r for r in rows if r["result"] != "PASS"]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} boards passed; results appended to {a.csv}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
