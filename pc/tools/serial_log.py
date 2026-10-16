"""Reset the board over USB and save everything it prints for a while (its
start-up messages).

python pc/main.py serial-log COM3 60 boot.log
"""

import argparse
import time

import serial

from utils.usb_serial import BAUD, reset_board


def capture(port_name: str, seconds: float, out_path: str) -> None:
    with serial.Serial(port_name, BAUD, timeout=0.5) as port:
        with open(out_path, "wb") as log:
            reset_board(port)
            deadline = time.time() + seconds
            while time.time() < deadline:
                log.write(port.read(4096))
                log.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("port", help="serial port, e.g. COM3")
    parser.add_argument("seconds", type=float, help="how long to record")
    parser.add_argument("out_file", help="file to write the log to")
    args = parser.parse_args()
    capture(args.port, args.seconds, args.out_file)
    print(f"saved {args.seconds:.0f} s of serial output to {args.out_file}")
    return 0
