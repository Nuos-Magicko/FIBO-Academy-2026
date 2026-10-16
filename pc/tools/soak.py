"""Long stability run: reset the board, log USB serial, receive the stream.

  python pc/main.py soak --port COM3 --minutes 60 --label desk
  python pc/main.py soak --port COM3 --minutes 60 --label test --host IP

Writes runs/soak_<time>_<label>/:
  serial.log     everything the board printed (reopened after USB
                 re-enumeration)
  metrics.jsonl  every METRICS record, keys flattened ("mem.int_min")
  host_stats.csv receiver stats every 10 s
  frames/        one JPEG + its boxes every --frame-every-s (camera images
                 can show people)

Pictures are matched with their boxes, and losses counted, by the same
Pipeline the viewer uses (analysis/pipeline.py).
  summary.json   resets, stream integrity, temperature range, memory minimum
                 first/last
"""

import argparse
import csv
import json
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import IO, Any

import serial

from utils.usb_serial import (
    BAUD,
    DEVICE_IP,
    NO_IP,
    RESET_REASON,
    clean_line,
    reset_board,
    split_lines,
)

from analysis.pipeline import Pipeline
from connection import protocol
from utils.paths import RUNS_DIR

HOST_STATS_EVERY_S = 10
IP_WAIT_S = 180
NO_DATA_TIMEOUT_S = 10
"""The board sends a METRICS record every second, so this long without data
means the connection is dead (for example after the Wi-Fi router went down),
even if it was never closed."""
HOST_STATS_HEADER = [
    "host_time",
    "elapsed_s",
    "frames",
    "rx_fps_10s",
    "rx_bytes_per_s_10s",
    "seq_gaps",
    "crc_errors",
    "resyncs",
    "missing_pairs",
    "reconnects",
    "device_boots",
]


class SerialLogger(threading.Thread):
    """Logs serial until stopped, reopening after USB re-enumeration; extracts
    the device IP and boots.

    boots counts "BOARD reset_reason" lines, so the soak's own reset at the
    start counts as one.
    """

    def __init__(self, port: str, path: Path) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.path = path
        self.ip: str | None = None
        self.ip_known = threading.Event()
        self.boots = 0
        self.reset_reasons: list[str] = []
        self.stop = threading.Event()

    def run(self) -> None:
        reset_pending = True
        with open(self.path, "ab") as log:
            while not self.stop.is_set():
                try:
                    with serial.Serial(self.port, BAUD, timeout=0.5) as port:
                        if reset_pending:
                            reset_board(port)
                            reset_pending = False
                        self._copy_until_stopped(port, log)
                except (serial.SerialException, OSError) as error:
                    now = datetime.now().isoformat()
                    message = f"\n[host {now}] serial error: {error}\n"
                    log.write(message.encode())
                    time.sleep(1)

    def _copy_until_stopped(self, port: serial.Serial, log: IO[bytes]) -> None:
        pending = b""
        while not self.stop.is_set():
            data = port.read(4096)
            if not data:
                continue
            log.write(data)
            log.flush()
            lines, pending = split_lines(pending, data)
            for raw in lines:
                self._scan(clean_line(raw))

    def _scan(self, line: str) -> None:
        ip_match = DEVICE_IP.search(line)
        if ip_match is not None and ip_match.group(1) != NO_IP:
            self.ip = ip_match.group(1)
            self.ip_known.set()
        reset_match = RESET_REASON.search(line)
        if reset_match is not None:
            self.boots += 1
            self.reset_reasons.append(reset_match.group(1))


class StreamReceiver:
    """Receives the TCP stream, reconnecting as needed, and keeps the soak's
    counters and files."""

    def __init__(
        self,
        host: str,
        port: int,
        out: Path,
        frame_every_s: float,
        serial_logger: SerialLogger,
    ) -> None:
        self.host = host
        self.port = port
        self.out = out
        self.frame_every_s = frame_every_s
        self.serial_logger = serial_logger
        self.parser = protocol.Parser()
        self.pipeline = Pipeline()
        self.stats = {
            "bytes": 0,
            "frames": 0,
            "metrics": 0,
            "reconnects": -1,
            "connect_failures": 0,
        }
        self.temps: list[Any] = []
        self.int_min: list[Any] = []
        self.ps_min: list[Any] = []
        self.last_metrics: dict[str, Any] = {}
        self.last_frame_id: int | None = None
        self.next_frame_save = 0.0
        self.started = time.time()
        self.sock: socket.socket | None = None
        self.last_data_time = time.time()
        self.metrics_file = open(out / "metrics.jsonl", "w")
        self.host_stats_file = open(out / "host_stats.csv", "w", newline="")
        self.host_stats = csv.writer(self.host_stats_file)
        self.host_stats.writerow(HOST_STATS_HEADER)
        self.window_frames = 0
        self.window_bytes = 0
        self.window_started = time.time()

    def run(self, minutes: float) -> None:
        deadline = self.started + minutes * 60
        while time.time() < deadline:
            if self.sock is None:
                connected = self._connect()
                if not connected:
                    continue
            data = self._receive()
            if data is None:
                continue
            self.stats["bytes"] += len(data)
            self.window_bytes += len(data)
            for record in self.parser.feed(data):
                self._handle(record)
            self.metrics_file.flush()
            self._write_host_stats_if_due()
        if self.sock is not None:
            self.sock.close()

    def _connect(self) -> bool:
        """Partial records from an old connection are dropped; the counters are
        kept. If the board printed a new IP address on serial, that one is
        used."""
        if self.serial_logger.ip is not None:
            self.host = self.serial_logger.ip
        try:
            sock = socket.create_connection((self.host, self.port), timeout=5)
            sock.settimeout(2)
            self.sock = sock
            self.last_data_time = time.time()
            self.stats["reconnects"] += 1
            self.pipeline.new_connection()
            self.parser.buffer.clear()
            return True
        except OSError:
            self.stats["connect_failures"] += 1
            time.sleep(2)
            return False

    def _receive(self) -> bytes | None:
        sock = self.sock
        if sock is None:
            return None
        try:
            data = sock.recv(65536)
            if not data:
                raise ConnectionError("closed")
            self.last_data_time = time.time()
            return data
        except socket.timeout:
            if time.time() - self.last_data_time > NO_DATA_TIMEOUT_S:
                sock.close()
                self.sock = None
                return None
            return b""
        except OSError:
            sock.close()
            self.sock = None
            return None

    def _handle(self, record: protocol.Record) -> None:
        self.pipeline.feed(record)
        if record.type == protocol.METRICS:
            self._log_metrics(record)
        if record.type == protocol.JPEG_FRAME:
            self.stats["frames"] += 1
            self.window_frames += 1
        latest = self.pipeline.latest
        if latest is not None and latest["frame_id"] != self.last_frame_id:
            self.last_frame_id = latest["frame_id"]
            self._save_frame_if_due(latest)

    def _log_metrics(self, record: protocol.Record) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        metrics: dict[str, Any] = {"host_time": now}
        metrics.update(protocol.flatten(protocol.decode_json(record.payload)))
        self.metrics_file.write(json.dumps(metrics) + "\n")
        self.stats["metrics"] += 1
        self.temps.append(metrics.get("temp_c"))
        self.int_min.append(metrics.get("mem.int_min"))
        self.ps_min.append(metrics.get("mem.ps_min"))
        self.last_metrics = metrics

    def _save_frame_if_due(self, frame: dict[str, Any]) -> None:
        """Saves the picture and its boxes once every --frame-every-s."""
        if time.time() < self.next_frame_save:
            return
        if frame["jpeg"] is None:
            return
        elapsed_s = int(time.time() - self.started)
        stem = self.out / "frames" / f"{elapsed_s:05d}s_f{frame['frame_id']}"
        stem.with_suffix(".jpg").write_bytes(frame["jpeg"])
        frame_info = {}
        for key, value in frame.items():
            if key != "jpeg":
                frame_info[key] = value
        stem.with_suffix(".json").write_text(json.dumps(frame_info, indent=1))
        self.next_frame_save = time.time() + self.frame_every_s

    def _write_host_stats_if_due(self) -> None:
        now = time.time()
        window = now - self.window_started
        if window < HOST_STATS_EVERY_S:
            return
        fps = self.window_frames / window
        self.host_stats.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                round(now - self.started),
                self.stats["frames"],
                round(fps, 2),
                round(self.window_bytes / window),
                self.pipeline.stats["seq_gaps"],
                self.parser.crc_errors,
                self.parser.resyncs,
                self.pipeline.stats["missing_pairs"],
                self.stats["reconnects"],
                self.serial_logger.boots,
            ]
        )
        self.host_stats_file.flush()
        metrics = self.last_metrics
        print(
            f"[{round(now - self.started):5d}s] fps={fps:.2f}"
            f" temp={metrics.get('temp_c')}"
            f" int_min={metrics.get('mem.int_min')}"
            f" ps_min={metrics.get('mem.ps_min')}"
            f" dropped={metrics.get('counters.tx_dropped')}"
            f" boots={self.serial_logger.boots}",
            flush=True,
        )
        self.window_frames = 0
        self.window_bytes = 0
        self.window_started = now

    def summary(self, label: str) -> dict[str, Any]:
        elapsed = time.time() - self.started
        boots = self.serial_logger.boots
        temp_c_min_max = None
        if self.temps:
            temp_c_min_max = [min(self.temps), max(self.temps)]
        int_min_first_last = None
        if self.int_min:
            int_min_first_last = [self.int_min[0], self.int_min[-1]]
        ps_min_first_last = None
        if self.ps_min:
            ps_min_first_last = [self.ps_min[0], self.ps_min[-1]]
        return {
            "label": label,
            "host": self.host,
            "started": datetime.fromtimestamp(self.started).isoformat(
                timespec="seconds"
            ),
            "duration_s": round(elapsed),
            "frames_received": self.stats["frames"],
            "rx_fps_mean": round(self.stats["frames"] / elapsed, 2),
            "rx_bytes_per_s_mean": round(self.stats["bytes"] / elapsed),
            "seq_gaps": self.pipeline.stats["seq_gaps"],
            "crc_errors": self.parser.crc_errors,
            "resyncs": self.parser.resyncs,
            "oversize": self.parser.oversize,
            "missing_pairs": self.pipeline.stats["missing_pairs"],
            "reconnects": self.stats["reconnects"],
            "connect_failures": self.stats["connect_failures"],
            "device_boots_seen": boots,
            "unexpected_resets": max(0, boots - 1),
            "reset_reasons": self.serial_logger.reset_reasons,
            "temp_c_min_max": temp_c_min_max,
            "int_min_first_last": int_min_first_last,
            "ps_min_first_last": ps_min_first_last,
            "device_last_metrics": self.last_metrics,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--label", default="soak")
    parser.add_argument(
        "--host", help="device IP (default: read from serial log)"
    )
    parser.add_argument("--tcp-port", type=int, default=5000)
    parser.add_argument("--frame-every-s", type=float, default=600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RUNS_DIR / f"soak_{started}_{args.label}"
    (out / "frames").mkdir(parents=True)
    serial_logger = SerialLogger(args.port, out / "serial.log")
    serial_logger.start()

    host = args.host
    if not host:
        print(
            "waiting for device IP on serial (boot self-tests take ~45 s)..."
        )
        ip_seen = serial_logger.ip_known.wait(IP_WAIT_S)
        if not ip_seen:
            print(f"no IP seen on serial within {IP_WAIT_S} s; see serial.log")
            serial_logger.stop.set()
            return 2
        host = serial_logger.ip
    print(f"device {host}:{args.tcp_port}, logging to {out}")

    receiver = StreamReceiver(
        host, args.tcp_port, out, args.frame_every_s, serial_logger
    )
    receiver.run(args.minutes)
    serial_logger.stop.set()
    time.sleep(1)
    summary = receiver.summary(args.label)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0
