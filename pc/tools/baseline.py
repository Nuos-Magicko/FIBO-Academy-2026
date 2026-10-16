"""Baseline collection: every mode x N repeats, fixed duration after warm-up, with full provenance.

  python pc/main.py baseline --host 192.168.1.248 --room-temp-c 26.5 --scene static_silhouettes \
      --heatsink yes --repeats 3 --seconds 60

Before the first trial the board streams in full_stream for --initial-warmup-s (default 120 s, handoff
section 9) so the die temperature settles. Each trial: set mode -> wait --warmup-s -> log --seconds of
records into its own run folder. Writes baselines/<date>_<device>_<scene>/:
  baseline.json  provenance: board MAC, sensor, fw hash, versions, config, room temp, heatsink, scene, date
  summary.csv    one row per trial (fps, stage means, mean of per-window p95, drops, temperature, memory)
  <mode>_r<k>/   the normal run log of that trial (see pc/logger.py)
Nothing is tuned or compared here; the numbers are for students to explain.
"""
import argparse
import csv
import json
import socket
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from analysis.pipeline import Pipeline
from connection import protocol as P
from recording.logger import RunLogger
from utils.paths import BASELINES_DIR

MODES = ["full_stream", "metadata_stream", "detect_no_stream", "camera_only", "inference_only"]
TIMED_STAGES = ("t_wait_frame", "t_decode", "t_pre", "t_infer", "t_post", "t_proc", "t_send")
SUMMARY_COLUMNS = ["mode", "repeat", "seconds", "windows", "fps_processed_mean", "fps_sent_mean", "capture_fps",
                   *[f"{stage}_mean_us" for stage in TIMED_STAGES[:-1]], "t_proc_p95_mean_us", "t_send_mean_us",
                   "tx_dropped", "rx_pairs", "rx_det_only", "seq_gaps", "crc_errors", "missing_pairs",
                   "frame_id_gaps", "temp_start_c", "temp_end_c", "temp_max_c", "int_min_end", "ps_min_end",
                   "rssi_mean", "run_dir"]
UNLIMITED_INFER_ONLY_RUNS = 65535
DEFAULT_INFER_ONLY_RUNS = 100
MODE_CONFIRM_TIMEOUT_S = 20
HOST_STATS_EVERY_S = 1
PROVENANCE_DEVICE_KEYS = ("device_id", "mac", "fw_version", "fw_build", "idf", "esp_dl", "esp32_camera",
                          "pedestrian_detect", "model", "chip_rev", "sensor_pid", "psram_bytes", "flash_bytes",
                          "reset_reason")


class Connection:
    """A TCP connection to the device yielding (bytes received, records completed) per chunk."""

    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.5)
        self.parser = P.Parser()

    def send(self, command):
        self.sock.sendall(P.encode_command(command))

    def chunks(self, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("device closed the connection")
            yield len(data), self.parser.feed(data)

    def close(self):
        self.sock.close()


def wait_for_mode(connection, pipeline, mode, timeout=MODE_CONFIRM_TIMEOUT_S):
    """Feed records until a HELLO confirms the mode.

    Waiting also matters before closing: closing right after a send, with unread data pending,
    resets the TCP connection and the device can discard the command.
    """
    for _, records in connection.chunks(timeout):
        for record in records:
            pipeline.feed(record)
        if pipeline.mode == mode:
            return
    raise TimeoutError(f"device did not confirm mode {mode}")


def weighted_stage_mean(windows, stage):
    """Sample-weighted mean of per-window means."""
    pairs = [(w.get(f"stages.{stage}.mean"), w.get(f"stages.{stage}.n")) for w in windows]
    pairs = [(mean, n) for mean, n in pairs if mean is not None and n]
    return round(sum(mean * n for mean, n in pairs) / sum(n for _, n in pairs)) if pairs else ""


def mean_of(windows, key, decimals=2):
    values = [w[key] for w in windows if w.get(key) is not None]
    return round(statistics.fmean(values), decimals) if values else ""


class Trial:
    """One mode for one repeat: switch, warm up unlogged, then log into its own run folder.

    The hand-over from warm-up to measurement happens only at a frame boundary: a DETECTIONS or
    FRAME_TIMING record belongs to the JPEG_FRAME before it, so it never starts a measurement.
    """

    def __init__(self, args, out, mode, repeat):
        self.args, self.out, self.mode, self.repeat = args, out, mode, repeat
        self.logger = RunLogger(root=out / f"{mode}_r{repeat}")
        self.windows = []

    def run(self):
        connection = Connection(self.args.host, self.args.port)
        warm = Pipeline()
        connection.send({"cmd": "set", "values": {"mode": self.mode, "infer_only_runs": UNLIMITED_INFER_ONLY_RUNS}})
        wait_for_mode(connection, warm, self.mode)
        measuring = self._measure(connection, warm)
        connection.close()
        self.logger.close()
        return self._summary_row(measuring, connection.parser)

    def _measure(self, connection, warm):
        measuring, measure_until = None, None
        switch_at = time.time() + self.args.warmup_s
        next_host_stats = time.time()
        for nbytes, records in connection.chunks(self.args.warmup_s + self.args.seconds + 5):
            for record in records:
                if measuring is None and time.time() >= switch_at and record.type not in (P.DETECTIONS,
                                                                                          P.FRAME_TIMING):
                    measuring = self._start_measuring(warm.hello)
                    measure_until = time.time() + self.args.seconds
                (measuring or warm).feed(record)
                if measuring and record.type == P.METRICS:
                    self.windows.append(P.flatten(P.decode_json(record.payload)))
            if measuring is None:
                continue
            measuring.stats["bytes"] += nbytes
            if time.time() >= measure_until:
                break
            if time.time() >= next_host_stats:
                measuring.host_stats(connection.parser)
                next_host_stats = time.time() + HOST_STATS_EVERY_S
        return measuring or warm

    def _start_measuring(self, hello):
        """The HELLO is logged first so the run folder is named after the device."""
        self.logger.record(P.Record(P.HELLO, 0, 0, 0, json.dumps(hello).encode()))
        measuring = Pipeline(logger=self.logger)
        measuring.hello = hello
        return measuring

    def _summary_row(self, pipeline, parser):
        windows = self.windows
        first, last = (windows[0], windows[-1]) if windows else ({}, {})
        span_s = (last.get("uptime_s", 0) - first.get("uptime_s", 0)) or 1
        temps = [round(w["temp_c"], 1) for w in windows if w.get("temp_c") is not None]
        return {
            "mode": self.mode, "repeat": self.repeat, "seconds": self.args.seconds, "windows": len(windows),
            "fps_processed_mean": mean_of(windows, "fps_processed"), "fps_sent_mean": mean_of(windows, "fps_sent"),
            "capture_fps": round((last.get("counters.cap_ok", 0) - first.get("counters.cap_ok", 0)) / span_s, 2),
            **{f"{stage}_mean_us": weighted_stage_mean(windows, stage) for stage in TIMED_STAGES},
            "t_proc_p95_mean_us": mean_of(windows, "stages.t_proc.p95", 0),
            "tx_dropped": last.get("counters.tx_dropped", 0) - first.get("counters.tx_dropped", 0),
            "rx_pairs": pipeline.stats["pairs"], "rx_det_only": pipeline.stats["det_only"],
            "seq_gaps": pipeline.stats["seq_gaps"], "crc_errors": parser.crc_errors,
            "missing_pairs": pipeline.stats["missing_pairs"], "frame_id_gaps": pipeline.stats["frame_id_gaps"],
            "temp_start_c": temps[0] if temps else "", "temp_end_c": temps[-1] if temps else "",
            "temp_max_c": max(temps) if temps else "",
            "int_min_end": last.get("mem.int_min", ""), "ps_min_end": last.get("mem.ps_min", ""),
            "rssi_mean": mean_of(windows, "rssi", 1),
            "run_dir": str(self.logger.dir.relative_to(self.out)) if self.logger.dir else "",
        }


def initial_warmup(args):
    """Stream full_stream so the die temperature settles; returns the device's HELLO."""
    connection = Connection(args.host, args.port)
    pipeline = Pipeline()
    connection.send({"cmd": "set", "values": {"mode": "full_stream"}})
    print(f"initial warm-up: {args.initial_warmup_s:.0f} s of full_stream", flush=True)
    for _, records in connection.chunks(args.initial_warmup_s):
        for record in records:
            pipeline.feed(record)
    connection.close()
    return pipeline.hello or {}


def provenance(args, hello, modes):
    return {"date": datetime.now().isoformat(timespec="seconds"), "scene": args.scene,
            "room_temp_c": args.room_temp_c, "heatsink": args.heatsink, "repeats": args.repeats,
            "seconds": args.seconds, "warmup_s": args.warmup_s, "initial_warmup_s": args.initial_warmup_s,
            "modes": modes, "note": args.note, "device": {key: hello.get(key) for key in PROVENANCE_DEVICE_KEYS},
            "config": hello.get("config")}


def restore_defaults(args):
    connection = Connection(args.host, args.port)
    connection.send({"cmd": "set", "values": {"mode": "full_stream", "infer_only_runs": DEFAULT_INFER_ONLY_RUNS}})
    wait_for_mode(connection, Pipeline(), "full_stream")
    connection.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--room-temp-c", required=True,
                        help="measured room temperature in C, or 'not_measured' (recorded as such; not a valid baseline)")
    parser.add_argument("--scene", required=True, help="e.g. static_silhouettes, walking_video, live_volunteers")
    parser.add_argument("--heatsink", choices=["yes", "no"], required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--warmup-s", type=float, default=15, help="after each mode switch")
    parser.add_argument("--initial-warmup-s", type=float, default=120, help="full_stream before the first trial")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--out", type=Path, default=BASELINES_DIR)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    if args.room_temp_c != "not_measured":
        args.room_temp_c = float(args.room_temp_c)
    return args


def main():
    args = parse_args()
    modes = args.modes.split(",")
    hello = initial_warmup(args)
    if not hello:
        sys.exit("no HELLO from the device")
    out = args.out / f"{datetime.now():%Y%m%d_%H%M}_{hello.get('device_id')}_{args.scene}"
    out.mkdir(parents=True)
    (out / "baseline.json").write_text(json.dumps(provenance(args, hello, modes), indent=1))
    if str(hello.get("fw_version", "")).endswith("-dirty"):
        print("WARNING: firmware was built from uncommitted changes (-dirty): not a valid baseline", flush=True)

    with open(out / "summary.csv", "w", newline="") as summary_file:
        writer = csv.DictWriter(summary_file, SUMMARY_COLUMNS)
        writer.writeheader()
        for repeat in range(1, args.repeats + 1):
            for mode in modes:
                print(f"trial {mode} r{repeat}: {args.warmup_s:.0f} s warm-up + {args.seconds:.0f} s", flush=True)
                row = Trial(args, out, mode, repeat).run()
                writer.writerow(row)
                summary_file.flush()
                print(f"  fps processed {row['fps_processed_mean']} sent {row['fps_sent_mean']} "
                      f"capture {row['capture_fps']}  t_proc {row['t_proc_mean_us']} us  temp {row['temp_end_c']} C",
                      flush=True)
    restore_defaults(args)
    print(f"done: {out}")
    return 0

