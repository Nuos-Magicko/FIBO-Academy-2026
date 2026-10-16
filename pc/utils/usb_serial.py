"""USB serial helpers shared by the scripts: board reset and boot-log parsing.

The XIAO's only USB port is the ESP32-S3 USB-Serial/JTAG unit. Toggling RTS while DTR is low
resets the chip, so a script can capture a boot log from its very first line.
"""
import re
import time

BAUD = 115200
RESET_PULSE_S = 0.1
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

DEVICE_IP = re.compile(r"NET mode=\w+ (?:ssid=.* )?ip=(\d+\.\d+\.\d+\.\d+)")
"""Firmware line announcing the address the TCP server is reachable at."""
RESET_REASON = re.compile(r"BOARD reset_reason=(\w+)")
"""Printed once per boot, so counting it counts boots."""
NO_IP = "0.0.0.0"


def reset_board(port):
    """Reset the chip through an open pyserial port (RTS pulse with DTR low)."""
    port.dtr = False
    port.rts = True
    time.sleep(RESET_PULSE_S)
    port.rts = False


def clean_line(raw):
    """Decode one raw log line and drop ANSI colour codes and surrounding whitespace."""
    return ANSI_ESCAPE.sub("", raw.decode("utf8", "replace")).strip()


def split_lines(pending, data):
    """Append data to a pending partial line; return (complete lines, new pending partial line)."""
    *lines, pending = (pending + data).split(b"\n")
    return lines, pending
