"""Connect to the board, optionally change settings, and check that everything
arrives correctly.

Prints one JSON summary: how many records of each kind arrived, whether every
picture got its own boxes, lost or damaged data, the board's messages and its
last measurements. Exit code 1 if pairing or integrity checks fail.

Check for 30 seconds:
  python pc/main.py check HOST --seconds 30
Change a setting:
  python pc/main.py check HOST --seconds 3 --set jpeg_quality=20
Pretend to be a slow reader (about 10 KB/s):
  python pc/main.py check HOST --seconds 30 --slow-ms 200 --recv-bytes 2048
Send any command:
  python pc/main.py check HOST \
      --cmd '{"cmd":"set","values":{"mode":"metadata_stream"}}'
Disconnect and reconnect 3 extra times:
  python pc/main.py check HOST --reconnects 3
Save one JPEG and its boxes (.json):
  python pc/main.py check HOST --save-frame frame.jpg
"""

import argparse
import json
import socket
import time
from typing import Any

from connection import protocol
from connection.clock_sync import ClockSync

SOCKET_POLL_S = 0.5
PING_INTERVAL_S = 1


def new_summary() -> dict[str, Any]:
    return {
        "sessions": 0,
        "bytes": 0,
        "counts": {},
        "seq_gaps": 0,
        "crc_errors": 0,
        "resyncs": 0,
        "pairs": 0,
        "pair_mismatch": 0,
        "unpaired_jpeg": 0,
        "det_without_jpeg": 0,
        "det_only": 0,
        "frame_id_gaps": 0,
        "boxes": 0,
        "server_closed": 0,
        "events": [],
        "hello": None,
        "last_metrics": None,
        "last_grid": None,
    }


class Session:
    """One TCP connection: counts records and checks that DETECTIONS follow
    their own JPEG_FRAME.

    pair_mismatch counts DETECTIONS[n+1] arriving right after JPEG_FRAME[n],
    which must never happen.
    """

    def __init__(
        self,
        args: argparse.Namespace,
        summary: dict[str, Any],
        clock: ClockSync,
    ) -> None:
        self.args = args
        self.summary = summary
        self.clock = clock
        self.sock = socket.create_connection((args.host, args.port), timeout=5)
        self.sock.settimeout(SOCKET_POLL_S)
        self.parser = protocol.Parser()
        self.last_seq: int | None = None
        self.jpeg_awaiting_detections: tuple[int, dict[str, Any]] | None = None
        self.last_frame_id: int | None = None
        self.commands_sent = False
        self.frame_saved = False

    def run(self, seconds: float) -> None:
        deadline = time.time() + seconds
        next_ping = time.time() + PING_INTERVAL_S
        while time.time() < deadline:
            data = self.receive()
            if data is None:
                break
            for record in self.parser.feed(data):
                self.count(record)
                self.handle(record)
            if self.args.ping and time.time() >= next_ping:
                self.sock.sendall(
                    protocol.encode_command(self.clock.ping_payload())
                )
                next_ping = time.time() + PING_INTERVAL_S
        self.summary["crc_errors"] += self.parser.crc_errors
        self.summary["resyncs"] += self.parser.resyncs
        self.sock.close()

    def receive(self) -> bytes | None:
        """Bytes received (possibly empty), or None when the device closed the
        connection."""
        try:
            data = self.sock.recv(self.args.recv_bytes)
            if not data:
                self.summary["server_closed"] += 1
                return None
        except socket.timeout:
            data = b""
        if self.args.slow_ms:
            time.sleep(self.args.slow_ms / 1000)
        self.summary["bytes"] += len(data)
        return data

    def handle(self, record: protocol.Record) -> None:
        if record.type == protocol.HELLO:
            self.on_hello(record)
        elif record.type == protocol.JPEG_FRAME:
            self.on_jpeg(record)
        elif record.type == protocol.DETECTIONS:
            self.on_detections(record)
        elif record.type == protocol.METRICS:
            self.on_metrics(record)
        elif record.type == protocol.EVENT:
            self.on_event(record)
        elif record.type == protocol.PONG:
            self.on_pong(record)
        elif record.type == protocol.PRESENCE_GRID:
            self.on_grid(record)

    def count(self, record: protocol.Record) -> None:
        counts = self.summary["counts"]
        counts[record.name] = counts.get(record.name, 0) + 1
        if self.last_seq is not None and record.seq != self.last_seq + 1:
            self.summary["seq_gaps"] += 1
        self.last_seq = record.seq

    def on_hello(self, record: protocol.Record) -> None:
        self.summary["hello"] = protocol.decode_json(record.payload)
        if "first_hello_config" not in self.summary:
            self.summary["first_hello_config"] = self.summary["hello"].get(
                "config"
            )
        if not self.commands_sent:
            for command in self.args.cmd:
                self.sock.sendall(protocol.encode_command(json.loads(command)))
            self.commands_sent = True

    def on_jpeg(self, record: protocol.Record) -> None:
        if self.jpeg_awaiting_detections is not None:
            self.summary["unpaired_jpeg"] += 1
        self.jpeg_awaiting_detections = (
            record.frame_id,
            protocol.decode_jpeg_frame(record.payload),
        )

    def on_detections(self, record: protocol.Record) -> None:
        detections = protocol.decode_detections(record.payload)
        mode = None
        if self.summary["hello"] is not None:
            mode = self.summary["hello"].get("config", {}).get("mode")
        if self.jpeg_awaiting_detections is not None:
            jpeg_frame_id, jpeg = self.jpeg_awaiting_detections
            if jpeg_frame_id == record.frame_id:
                self.summary["pairs"] += 1
                self.save_frame_once(jpeg, detections)
            else:
                self.summary["pair_mismatch"] += 1
            self.jpeg_awaiting_detections = None
        elif mode == "full_stream":
            self.summary["det_without_jpeg"] += 1
        else:
            self.summary["det_only"] += 1
        if (
            self.last_frame_id is not None
            and record.frame_id > self.last_frame_id + 1
        ):
            self.summary["frame_id_gaps"] += (
                record.frame_id - self.last_frame_id - 1
            )
        self.last_frame_id = record.frame_id
        self.summary["boxes"] += len(detections["boxes"])

    def save_frame_once(
        self, jpeg: dict[str, Any], detections: dict[str, Any]
    ) -> None:
        if not self.args.save_frame or self.frame_saved:
            return
        with open(self.args.save_frame, "wb") as image_file:
            image_file.write(jpeg["jpeg"])
        with open(self.args.save_frame + ".json", "w") as boxes_file:
            json.dump(detections, boxes_file, indent=1)
        self.frame_saved = True

    def on_metrics(self, record: protocol.Record) -> None:
        self.summary["last_metrics"] = protocol.flatten(
            protocol.decode_json(record.payload)
        )

    def on_event(self, record: protocol.Record) -> None:
        event = protocol.decode_json(record.payload)
        self.summary["events"].append(
            f'{event["severity"]}:{event["code"]}: {event["text"]}'
        )

    def on_pong(self, record: protocol.Record) -> None:
        self.clock.on_pong(protocol.decode_json(record.payload))

    def on_grid(self, record: protocol.Record) -> None:
        grid = protocol.decode_grid(record.payload)
        total = 0
        for counts_in_row in grid["counts"]:
            total += sum(counts_in_row)
        self.summary["last_grid"] = {
            "cols": grid["cols"],
            "rows": grid["rows"],
            "frames": grid["frames"],
            "elapsed_us": grid["elapsed_us"],
            "total": total,
        }


def finish_summary(
    summary: dict[str, Any], clock: ClockSync, elapsed: float
) -> None:
    summary["elapsed_s"] = round(elapsed, 1)
    summary["rx_fps"] = round(
        summary["counts"].get("DETECTIONS", 0) / elapsed, 2
    )
    if clock.samples:
        rtt, offset = min(clock.samples)
        summary["clock"] = {
            "n": len(clock.samples),
            "best_rtt_us": rtt,
            "offset_us": round(offset),
            "uncertainty_us": rtt / 2,
        }
    if summary["hello"]:
        hello = summary["hello"]
        summary["hello"] = {
            "device_id": hello.get("device_id"),
            "fw_version": hello.get("fw_version"),
            "sensor_pid": hello.get("sensor_pid"),
            "ip": hello.get("ip"),
            "config": hello.get("config"),
        }


def parse_setting(text: str) -> tuple[str, Any]:
    """ "jpeg_quality=20" -> ("jpeg_quality", 20). Numbers and true/false are
    converted; anything else stays text, so "mode=camera_only" works without
    quotes."""
    name, separator, value = text.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError(f"expected NAME=VALUE, got {text!r}")
    try:
        return name, json.loads(value)
    except json.JSONDecodeError:
        return name, value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument(
        "--slow-ms",
        type=float,
        default=0,
        help="sleep after every recv (test harness only)",
    )
    parser.add_argument(
        "--recv-bytes",
        type=int,
        default=65536,
        help="max bytes per recv (with --slow-ms: reader throughput cap)",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        type=parse_setting,
        metavar="NAME=VALUE",
        help="change a setting, e.g. --set jpeg_quality=20 (repeatable)",
    )
    parser.add_argument(
        "--cmd",
        action="append",
        default=[],
        help="COMMAND JSON sent after HELLO",
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="PING once per second; report clock offset",
    )
    parser.add_argument(
        "--reconnects",
        type=int,
        default=0,
        help="extra disconnect/reconnect cycles",
    )
    parser.add_argument("--save-frame")
    args = parser.parse_args()
    if args.set:
        set_command = {"cmd": "set", "values": dict(args.set)}
        args.cmd.insert(0, json.dumps(set_command))
    return args


def main() -> int:
    args = parse_args()
    summary = new_summary()
    clock = ClockSync(window=10_000)
    started = time.time()
    session_count = args.reconnects + 1
    for session_number in range(session_count):
        session = Session(args, summary, clock)
        session.run(args.seconds / session_count)
        summary["sessions"] += 1
    finish_summary(summary, clock, time.time() - started)
    print(json.dumps(summary, indent=1))
    if summary["pair_mismatch"] > 0:
        return 1
    if summary["crc_errors"] > 0:
        return 1
    if summary["seq_gaps"] > 0:
        return 1
    if summary["det_without_jpeg"] > 0:
        return 1
    return 0
