"""Reset the XIAO over USB-Serial/JTAG and save its serial log.

usage: python serial_capture.py PORT SECONDS OUTFILE
"""
import sys
import time

import serial

port, seconds, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
with serial.Serial(port, 115200, timeout=0.5) as s, open(out, "wb") as f:
    # USB-Serial/JTAG: RTS pulse with DTR low = chip reset
    s.dtr = False
    s.rts = True
    time.sleep(0.1)
    s.rts = False
    end = time.time() + seconds
    while time.time() < end:
        f.write(s.read(4096))
        f.flush()
