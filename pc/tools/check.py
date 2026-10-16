"""Connect to the board, optionally change settings, and check that everything arrives correctly.

Prints one JSON summary: how many records of each kind arrived, whether every picture got its
own boxes, lost or damaged data, the board's messages and its last measurements.
Exit code 1 if pairing or integrity checks fail.

  python pc/main.py check HOST --seconds 3 --set jpeg_quality=20        change a setting
  python pc/main.py check HOST --seconds 30
  python pc/main.py check HOST --seconds 30 --slow-ms 200 --recv-bytes 2048   slow reader (~10 KB/s)
  python pc/main.py check HOST --cmd '{"cmd":"set","values":{"mode":"metadata_stream"}}'
  python pc/main.py check HOST --reconnects 3                       disconnect/reconnect cycles
  python pc/main.py check HOST --save-frame frame.jpg               one JPEG + its boxes (.json)
"""
import argparse
import json
import socket
import time

from connection import protocol as P
from connection.clock_sync import ClockSync

SOCKET_POLL_S = 0.5
PING_INTERVAL_S = 1


def new_summary():
    return {"sessions": 0, "bytes": 0, "counts": {}, "seq_gaps": 0, "crc_errors": 0, "resyncs": 0,
            "pairs": 0, "pair_mismatch": 0, "unpaired_jpeg": 0, "det_without_jpeg": 0, "det_only": 0,
            "frame_id_gaps": 0, "boxes": 0, "server_closed": 0, "events": [], "hello": None,
            "last_metrics": None, "last_grid": None}


class Session:
    """One TCP connection: counts records and checks that DETECTIONS follow their own JPEG_FRAME.

    pair_mismatch counts DETECTIONS[n+1] arriving right after JPEG_FRAME[n], which must never happen.
    """

    def __init__(self, args, summary, clock):
        self.args, self.summary, self.clock = args, summary, clock
        self.sock = socket.create_connection((args.host, args.port), timeout=5)
        self.sock.settimeout(SOCKET_POLL_S)
        self.parser = P.Parser()
        self.last_seq = None
        self.jpeg_awaiting_detections = None
        self.last_frame_id = None
        self.commands_sent = False
        self.frame_saved = False
        self.handlers = {P.HELLO: self.on_hello, P.JPEG_FRAME: self.on_jpeg, P.DETECTIONS: self.on_detections,
                         P.METRICS: self.on_metrics, P.EVENT: self.on_event, P.PONG: self.on_pong,
                         P.PRESENCE_GRID: self.on_grid}

    def run(self, seconds):
        deadline = time.time() + seconds
        next_ping = time.time() + PING_INTERVAL_S
        while time.time() < deadline:
            data = self.receive()
            if data is None:
                break
            for record in self.parser.feed(data):
                self.count(record)
                handler = self.handlers.get(record.type)
                if handler:
                    handler(record)
            if self.args.ping and time.time() >= next_ping:
                self.sock.sendall(P.encode_command(self.clock.ping_payload()))
                next_ping = time.time() + PING_INTERVAL_S
        self.summary["crc_errors"] += self.parser.crc_errors
        self.summary["resyncs"] += self.parser.resyncs
        self.sock.close()

    def receive(self):
        """Bytes received (possibly empty), or None when the device closed the connection."""
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

    def count(self, record):
        counts = self.summary["counts"]
        counts[record.name] = counts.get(record.name, 0) + 1
        if self.last_seq is not None and record.seq != self.last_seq + 1:
            self.summary["seq_gaps"] += 1
        self.last_seq = record.seq

    def on_hello(self, record):
        self.summary["hello"] = P.decode_json(record.payload)
        self.summary.setdefault("first_hello_config", self.summary["hello"].get("config"))
        if not self.commands_sent:
            for command in self.args.cmd:
                self.sock.sendall(P.encode_command(json.loads(command)))
            self.commands_sent = True

    def on_jpeg(self, record):
        if self.jpeg_awaiting_detections is not None:
            self.summary["unpaired_jpeg"] += 1
        self.jpeg_awaiting_detections = (record.frame_id, P.decode_jpeg_frame(record.payload))

    def on_detections(self, record):
        detections = P.decode_detections(record.payload)
        mode = (self.summary.get("hello") or {}).get("config", {}).get("mode")
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
        if self.last_frame_id is not None and record.frame_id > self.last_frame_id + 1:
            self.summary["frame_id_gaps"] += record.frame_id - self.last_frame_id - 1
        self.last_frame_id = record.frame_id
        self.summary["boxes"] += len(detections["boxes"])

    def save_frame_once(self, jpeg, detections):
        if not self.args.save_frame or self.frame_saved:
            return
        with open(self.args.save_frame, "wb") as image_file:
            image_file.write(jpeg["jpeg"])
        with open(self.args.save_frame + ".json", "w") as boxes_file:
            json.dump(detections, boxes_file, indent=1)
        self.frame_saved = True

    def on_metrics(self, record):
        self.summary["last_metrics"] = P.flatten(P.decode_json(record.payload))

    def on_event(self, record):
        event = P.decode_json(record.payload)
        self.summary["events"].append(f'{event["severity"]}:{event["code"]}: {event["text"]}')

    def on_pong(self, record):
        self.clock.on_pong(P.decode_json(record.payload))

    def on_grid(self, record):
        grid = P.decode_grid(record.payload)
        self.summary["last_grid"] = {key: grid[key] for key in ("cols", "rows", "frames", "elapsed_us")}
        self.summary["last_grid"]["total"] = sum(map(sum, grid["counts"]))


def finish_summary(summary, clock, elapsed):
    summary["elapsed_s"] = round(elapsed, 1)
    summary["rx_fps"] = round(summary["counts"].get("DETECTIONS", 0) / elapsed, 2)
    if clock.samples:
        rtt, offset = min(clock.samples)
        summary["clock"] = {"n": len(clock.samples), "best_rtt_us": rtt, "offset_us": round(offset),
                            "uncertainty_us": rtt / 2}
    if summary["hello"]:
        hello = summary["hello"]
        summary["hello"] = {key: hello.get(key) for key in ("device_id", "fw_version", "sensor_pid", "ip", "config")}


def parse_setting(text):
    """"jpeg_quality=20" -> ("jpeg_quality", 20). Numbers and true/false are converted; anything else
    stays text, so "mode=camera_only" works without quotes."""
    name, separator, value = text.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError(f"expected NAME=VALUE, got {text!r}")
    try:
        return name, json.loads(value)
    except json.JSONDecodeError:
        return name, value


def settings_command(settings):
    return json.dumps({"cmd": "set", "values": dict(settings)})


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--slow-ms", type=float, default=0, help="sleep after every recv (test harness only)")
    parser.add_argument("--recv-bytes", type=int, default=65536,
                        help="max bytes per recv (with --slow-ms: reader throughput cap)")
    parser.add_argument("--set", action="append", default=[], type=parse_setting, metavar="NAME=VALUE",
                        help="change a setting, e.g. --set jpeg_quality=20 (repeatable)")
    parser.add_argument("--cmd", action="append", default=[], help="COMMAND JSON sent after HELLO")
    parser.add_argument("--ping", action="store_true", help="PING once per second; report clock offset")
    parser.add_argument("--reconnects", type=int, default=0, help="extra disconnect/reconnect cycles")
    parser.add_argument("--save-frame")
    args = parser.parse_args()
    if args.set:
        args.cmd.insert(0, settings_command(args.set))
    return args


def main():
    args = parse_args()
    summary, clock = new_summary(), ClockSync(window=10_000)
    started = time.time()
    for _ in range(args.reconnects + 1):
        Session(args, summary, clock).run(args.seconds / (args.reconnects + 1))
        summary["sessions"] += 1
    finish_summary(summary, clock, time.time() - started)
    print(json.dumps(summary, indent=1))
    failed = summary["pair_mismatch"] or summary["crc_errors"] or summary["seq_gaps"] or summary["det_without_jpeg"]
    return 1 if failed else 0

