"""Gate 1.4 soak: reset the board, log USB serial, receive the TCP stream, summarize.

usage: python scripts/soak.py --port COM3 --minutes 60 --label noheatsink [--host IP]

Writes runs/soak_<time>_<label>/: serial.log, metrics.jsonl (device METRICS, flattened keys),
host_stats.csv (receiver, every 10 s), frames/ (JPEG + boxes every 10 min), summary.json.
Needs pyserial (present in the ESP-IDF Python env).
"""
import argparse
import csv
import json
import re
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pc"))
import protocol  # noqa: E402

ANSI = re.compile(rb"\x1b\[[0-9;]*m")


class SerialLogger(threading.Thread):
    """Logs serial forever (reopens after USB re-enumeration), extracts device IP and resets."""

    def __init__(self, port, path):
        super().__init__(daemon=True)
        self.port, self.path = port, path
        self.ip = None
        self.ip_event = threading.Event()
        self.boots = 0  # "BOARD reset_reason" lines; 1 is the soak's own reset
        self.reset_reasons = []
        self.stop = threading.Event()

    def run(self):
        first = True
        with open(self.path, "ab") as f:
            while not self.stop.is_set():
                try:
                    with serial.Serial(self.port, 115200, timeout=0.5) as s:
                        if first:  # USB-Serial/JTAG: RTS pulse with DTR low = chip reset
                            s.dtr = False
                            s.rts = True
                            time.sleep(0.1)
                            s.rts = False
                            first = False
                        pending = b""
                        while not self.stop.is_set():
                            data = s.read(4096)
                            if not data:
                                continue
                            f.write(data)
                            f.flush()
                            pending += data
                            *lines, pending = pending.split(b"\n")
                            for line in lines:
                                self._scan(ANSI.sub(b"", line).decode("utf8", "replace"))
                except (serial.SerialException, OSError) as e:
                    f.write(f"\n[host {datetime.now().isoformat()}] serial error: {e}\n".encode())
                    time.sleep(1)

    def _scan(self, line):
        m = re.search(r"NET mode=\w+ (?:ssid=\S+ )?ip=(\d+\.\d+\.\d+\.\d+)", line)
        if m and m.group(1) != "0.0.0.0":
            self.ip = m.group(1)
            self.ip_event.set()
        m = re.search(r"BOARD reset_reason=(\w+)", line)
        if m:
            self.boots += 1
            self.reset_reasons.append(m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--label", default="soak")
    ap.add_argument("--host", help="device IP (default: read from serial log)")
    ap.add_argument("--tcp-port", type=int, default=5000)
    ap.add_argument("--frame-every-s", type=float, default=600)
    a = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    out = root / "runs" / f"soak_{datetime.now():%Y%m%d_%H%M%S}_{a.label}"
    (out / "frames").mkdir(parents=True)
    ser = SerialLogger(a.port, out / "serial.log")
    ser.start()

    host = a.host
    if not host:
        print("waiting for device IP on serial (boot self-tests take ~45 s)...")
        if not ser.ip_event.wait(180):
            print("no IP seen on serial within 180 s; see serial.log")
            ser.stop.set()
            return 2
        host = ser.ip
    print(f"device {host}:{a.tcp_port}, logging to {out}")

    t_start = time.time()
    t_end = t_start + a.minutes * 60
    parser = protocol.Parser()
    stats = dict(records=0, bytes=0, frames=0, det=0, metrics=0, seq_gaps=0, missing_pairs=0,
                 reconnects=-1, connect_failures=0)
    temps, int_min, ps_min, last_metrics = [], [], [], {}
    pending = {}  # frame_id -> set of record names, for pairing
    last_seq = None
    next_frame_save = 0.0
    mf = open(out / "metrics.jsonl", "w")
    hf = open(out / "host_stats.csv", "w", newline="")
    hw = csv.writer(hf)
    hw.writerow(["host_time", "elapsed_s", "frames", "rx_fps_10s", "rx_bytes_per_s_10s", "seq_gaps",
                 "crc_errors", "resyncs", "missing_pairs", "reconnects", "device_boots"])
    win_frames, win_bytes, win_t = 0, 0, time.time()

    sock = None
    while time.time() < t_end:
        if sock is None:
            try:
                sock = socket.create_connection((host, a.tcp_port), timeout=5)
                sock.settimeout(2)
                stats["reconnects"] += 1
                last_seq = None
                parser.buf.clear()  # drop half a record from the old connection, keep counters
            except OSError:
                stats["connect_failures"] += 1
                time.sleep(2)
                continue
        try:
            data = sock.recv(65536)
            if not data:
                raise ConnectionError("closed")
        except socket.timeout:
            data = b""
        except OSError:
            sock.close()
            sock = None
            continue

        stats["bytes"] += len(data)
        win_bytes += len(data)
        for r in parser.feed(data):
            stats["records"] += 1
            if last_seq is not None and r.seq != (last_seq + 1) & 0xFFFFFFFF:
                stats["seq_gaps"] += 1
            last_seq = r.seq
            if r.name == "METRICS":
                m = protocol.flatten(protocol.decode_json(r.payload))
                m = {"host_time": datetime.now().isoformat(timespec="seconds"), **m}
                mf.write(json.dumps(m) + "\n")
                stats["metrics"] += 1
                temps.append(m.get("temp_c"))
                int_min.append(m.get("mem.int_min", m.get("int_min")))
                ps_min.append(m.get("mem.ps_min", m.get("ps_min")))
                last_metrics = m
            elif r.name in ("JPEG_FRAME", "DETECTIONS"):
                pending.setdefault(r.frame_id, {})[r.name] = r
                if r.name == "JPEG_FRAME":
                    stats["frames"] += 1
                    win_frames += 1
                else:
                    stats["det"] += 1
                got = pending[r.frame_id]
                if len(got) == 2:
                    if time.time() >= next_frame_save:
                        jf = protocol.decode_jpeg_frame(got["JPEG_FRAME"].payload)
                        dets = protocol.decode_detections(got["DETECTIONS"].payload)
                        stem = out / "frames" / f"{int(time.time() - t_start):05d}s_f{r.frame_id}"
                        stem.with_suffix(".jpg").write_bytes(jf["jpeg"])
                        stem.with_suffix(".json").write_text(json.dumps(
                            {k: v for k, v in jf.items() if k != "jpeg"} | dets, indent=1))
                        next_frame_save = time.time() + a.frame_every_s
                    del pending[r.frame_id]
                # anything 10+ frames older than this one never got its partner
                for fid in [f for f in pending if f < r.frame_id - 10]:
                    stats["missing_pairs"] += 1
                    del pending[fid]
        mf.flush()

        now = time.time()
        if now - win_t >= 10:
            hw.writerow([datetime.now().isoformat(timespec="seconds"), round(now - t_start), stats["frames"],
                         round(win_frames / (now - win_t), 2), round(win_bytes / (now - win_t)),
                         stats["seq_gaps"], parser.crc_errors, parser.resyncs, stats["missing_pairs"],
                         stats["reconnects"], ser.boots])
            hf.flush()
            print(f"[{round(now - t_start):5d}s] fps={win_frames / (now - win_t):.2f} "
                  f"temp={last_metrics.get('temp_c')} int_min={last_metrics.get('mem.int_min')} "
                  f"ps_min={last_metrics.get('mem.ps_min')} dropped={last_metrics.get('counters.tx_dropped')} "
                  f"boots={ser.boots}", flush=True)
            win_frames, win_bytes, win_t = 0, 0, now

    if sock:
        sock.close()
    ser.stop.set()
    time.sleep(1)
    elapsed = time.time() - t_start
    summary = {
        "label": a.label, "host": host, "started": datetime.fromtimestamp(t_start).isoformat(timespec="seconds"),
        "duration_s": round(elapsed), "frames_received": stats["frames"],
        "rx_fps_mean": round(stats["frames"] / elapsed, 2), "rx_bytes_per_s_mean": round(stats["bytes"] / elapsed),
        "seq_gaps": stats["seq_gaps"], "crc_errors": parser.crc_errors, "resyncs": parser.resyncs,
        "oversize": parser.oversize, "missing_pairs": stats["missing_pairs"], "reconnects": stats["reconnects"],
        "connect_failures": stats["connect_failures"],
        "device_boots_seen": ser.boots, "unexpected_resets": max(0, ser.boots - 1),
        "reset_reasons": ser.reset_reasons,
        "temp_c_min_max": [min(temps), max(temps)] if temps else None,
        "int_min_first_last": [int_min[0], int_min[-1]] if int_min else None,
        "ps_min_first_last": [ps_min[0], ps_min[-1]] if ps_min else None,
        "device_last_metrics": last_metrics,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
