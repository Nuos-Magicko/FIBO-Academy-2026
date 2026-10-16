"""Baseline collection: every mode x N repeats, with full provenance.

  python pc/main.py baseline --host 192.168.1.248 --room-temp-c 26.5 \
      --scene static_silhouettes --heatsink no --repeats 3 --seconds 60

Before the first trial the board streams in full_stream for
--initial-warmup-s (default 120 s, handoff section 9) so the die temperature
settles. Each trial: set mode -> wait --warmup-s -> log --seconds of records
into its own run folder. Writes baselines/<date>_<device>_<scene>/:
  baseline.json  provenance: board MAC, sensor, fw hash, versions, config,
                 room temp, heatsink, scene, date
  summary.csv    one row per trial (fps, stage means, mean of per-window p95,
                 drops, temperature, memory)
  <mode>_r<k>/   the normal run log of that trial (see recording/logger.py)
Nothing is tuned or compared here; the numbers are for students to explain.
"""

import argparse
import csv
import json
import socket
import statistics
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis.pipeline import Pipeline
from connection import protocol
from recording.logger import RunLogger
from utils.paths import BASELINES_DIR

TIMED_STAGES = (
    "t_wait_frame",
    "t_decode",
    "t_pre",
    "t_infer",
    "t_post",
    "t_proc",
    "t_send",
)
SUMMARY_COLUMNS = [
    "mode",
    "repeat",
    "seconds",
    "windows",
    "fps_processed_mean",
    "fps_sent_mean",
    "capture_fps",
    "t_wait_frame_mean_us",
    "t_decode_mean_us",
    "t_pre_mean_us",
    "t_infer_mean_us",
    "t_post_mean_us",
    "t_proc_mean_us",
    "t_proc_p95_mean_us",
    "t_send_mean_us",
    "tx_dropped",
    "rx_pairs",
    "rx_det_only",
    "seq_gaps",
    "crc_errors",
    "missing_pairs",
    "frame_id_gaps",
    "temp_start_c",
    "temp_end_c",
    "temp_max_c",
    "int_min_end",
    "ps_min_end",
    "rssi_mean",
    "run_dir",
]
UNLIMITED_INFER_ONLY_RUNS = 65535
DEFAULT_INFER_ONLY_RUNS = 100
MODE_CONFIRM_TIMEOUT_S = 20
HOST_STATS_EVERY_S = 1
PROVENANCE_DEVICE_KEYS = (
    "device_id",
    "mac",
    "fw_version",
    "fw_build",
    "idf",
    "esp_dl",
    "esp32_camera",
    "pedestrian_detect",
    "model",
    "chip_rev",
    "sensor_pid",
    "psram_bytes",
    "flash_bytes",
    "reset_reason",
)


class Connection:
    """A TCP connection to the device yielding (bytes received, records
    completed) per chunk."""

    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.5)
        self.parser = protocol.Parser()

    def send(self, command: dict[str, Any]) -> None:
        self.sock.sendall(protocol.encode_command(command))

    def chunks(
        self, seconds: float
    ) -> Iterator[tuple[int, list[protocol.Record]]]:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("device closed the connection")
            yield len(data), self.parser.feed(data)

    def close(self) -> None:
        self.sock.close()


def wait_for_mode(
    connection: Connection,
    pipeline: Pipeline,
    mode: str,
    timeout: float = MODE_CONFIRM_TIMEOUT_S,
) -> None:
    """Feed records until a HELLO confirms the mode.

    Waiting also matters before closing: closing right after a send, with
    unread data pending, resets the TCP connection and the device can discard
    the command.
    """
    for nbytes, records in connection.chunks(timeout):
        for record in records:
            pipeline.feed(record)
        if pipeline.mode == mode:
            return
    raise TimeoutError(f"device did not confirm mode {mode}")


def weighted_stage_mean(
    windows: list[dict[str, Any]], stage: str
) -> int | str:
    """Sample-weighted mean of per-window means."""
    weighted_sum = 0
    sample_count = 0
    for window in windows:
        mean = window.get(f"stages.{stage}.mean")
        samples = window.get(f"stages.{stage}.n")
        if mean is not None and samples:
            weighted_sum += mean * samples
            sample_count += samples
    if sample_count == 0:
        return ""
    return round(weighted_sum / sample_count)


def mean_of(
    windows: list[dict[str, Any]], key: str, decimals: int = 2
) -> float | str:
    values = []
    for window in windows:
        if window.get(key) is not None:
            values.append(window[key])
    if not values:
        return ""
    return round(statistics.fmean(values), decimals)


class Trial:
    """One mode for one repeat: switch, warm up unlogged, then log into its own
    run folder.

    The hand-over from warm-up to measurement happens only at a frame boundary:
    a DETECTIONS or FRAME_TIMING record belongs to the JPEG_FRAME before it, so
    it never starts a measurement.
    """

    def __init__(
        self, args: argparse.Namespace, out: Path, mode: str, repeat: int
    ) -> None:
        self.args = args
        self.out = out
        self.mode = mode
        self.repeat = repeat
        self.logger = RunLogger(root=out / f"{mode}_r{repeat}")
        self.windows: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        connection = Connection(self.args.host, self.args.port)
        warm = Pipeline()
        connection.send(
            {
                "cmd": "set",
                "values": {
                    "mode": self.mode,
                    "infer_only_runs": UNLIMITED_INFER_ONLY_RUNS,
                },
            }
        )
        wait_for_mode(connection, warm, self.mode)
        measuring = self._measure(connection, warm)
        connection.close()
        self.logger.close()
        return self._summary_row(measuring, connection.parser)

    def _measure(self, connection: Connection, warm: Pipeline) -> Pipeline:
        measuring: Pipeline | None = None
        measure_until = 0.0
        switch_at = time.time() + self.args.warmup_s
        next_host_stats = time.time()
        for nbytes, records in connection.chunks(
            self.args.warmup_s + self.args.seconds + 5
        ):
            for record in records:
                starts_a_frame = (
                    record.type != protocol.DETECTIONS
                    and record.type != protocol.FRAME_TIMING
                )
                if (
                    measuring is None
                    and time.time() >= switch_at
                    and starts_a_frame
                ):
                    measuring = self._start_measuring(warm.hello)
                    measure_until = time.time() + self.args.seconds
                if measuring is None:
                    warm.feed(record)
                else:
                    measuring.feed(record)
                    if record.type == protocol.METRICS:
                        self.windows.append(
                            protocol.flatten(
                                protocol.decode_json(record.payload)
                            )
                        )
            if measuring is None:
                continue
            measuring.stats["bytes"] += nbytes
            if time.time() >= measure_until:
                break
            if time.time() >= next_host_stats:
                measuring.host_stats(connection.parser)
                next_host_stats = time.time() + HOST_STATS_EVERY_S
        if measuring is None:
            return warm
        return measuring

    def _start_measuring(self, hello: dict[str, Any] | None) -> Pipeline:
        """The HELLO is logged first so the run folder is named after the
        device.
        """
        self.logger.record(
            protocol.Record(
                protocol.HELLO, 0, 0, 0, json.dumps(hello).encode()
            )
        )
        measuring = Pipeline(logger=self.logger)
        measuring.hello = hello
        return measuring

    def _summary_row(
        self, pipeline: Pipeline, parser: protocol.Parser
    ) -> dict[str, Any]:
        windows = self.windows
        first: dict[str, Any] = {}
        last: dict[str, Any] = {}
        if windows:
            first = windows[0]
            last = windows[-1]
        span_s = last.get("uptime_s", 0) - first.get("uptime_s", 0)
        if span_s == 0:
            span_s = 1
        temps = []
        for window in windows:
            if window.get("temp_c") is not None:
                temps.append(round(window["temp_c"], 1))
        temp_start_c = ""
        temp_end_c = ""
        temp_max_c = ""
        if temps:
            temp_start_c = temps[0]
            temp_end_c = temps[-1]
            temp_max_c = max(temps)
        run_dir = ""
        if self.logger.dir is not None:
            run_dir = str(self.logger.dir.relative_to(self.out))
        captured = last.get("counters.cap_ok", 0) - first.get(
            "counters.cap_ok", 0
        )

        row = {
            "mode": self.mode,
            "repeat": self.repeat,
            "seconds": self.args.seconds,
            "windows": len(windows),
            "fps_processed_mean": mean_of(windows, "fps_processed"),
            "fps_sent_mean": mean_of(windows, "fps_sent"),
            "capture_fps": round(captured / span_s, 2),
            "t_proc_p95_mean_us": mean_of(windows, "stages.t_proc.p95", 0),
            "tx_dropped": last.get("counters.tx_dropped", 0)
            - first.get("counters.tx_dropped", 0),
            "rx_pairs": pipeline.stats["pairs"],
            "rx_det_only": pipeline.stats["det_only"],
            "seq_gaps": pipeline.stats["seq_gaps"],
            "crc_errors": parser.crc_errors,
            "missing_pairs": pipeline.stats["missing_pairs"],
            "frame_id_gaps": pipeline.stats["frame_id_gaps"],
            "temp_start_c": temp_start_c,
            "temp_end_c": temp_end_c,
            "temp_max_c": temp_max_c,
            "int_min_end": last.get("mem.int_min", ""),
            "ps_min_end": last.get("mem.ps_min", ""),
            "rssi_mean": mean_of(windows, "rssi", 1),
            "run_dir": run_dir,
        }
        for stage in TIMED_STAGES:
            row[f"{stage}_mean_us"] = weighted_stage_mean(windows, stage)
        return row


def initial_warmup(args: argparse.Namespace) -> dict[str, Any]:
    """Stream full_stream so the die temperature settles; returns the device's
    HELLO."""
    connection = Connection(args.host, args.port)
    pipeline = Pipeline()
    connection.send({"cmd": "set", "values": {"mode": "full_stream"}})
    print(
        f"initial warm-up: {args.initial_warmup_s:.0f} s of full_stream",
        flush=True,
    )
    for nbytes, records in connection.chunks(args.initial_warmup_s):
        for record in records:
            pipeline.feed(record)
    connection.close()
    if pipeline.hello is None:
        return {}
    return pipeline.hello


def provenance(
    args: argparse.Namespace, hello: dict[str, Any], modes: list[str]
) -> dict[str, Any]:
    device = {}
    for key in PROVENANCE_DEVICE_KEYS:
        device[key] = hello.get(key)
    return {
        "date": datetime.now().isoformat(timespec="seconds"),
        "scene": args.scene,
        "room_temp_c": args.room_temp_c,
        "heatsink": args.heatsink,
        "repeats": args.repeats,
        "seconds": args.seconds,
        "warmup_s": args.warmup_s,
        "initial_warmup_s": args.initial_warmup_s,
        "modes": modes,
        "note": args.note,
        "device": device,
        "config": hello.get("config"),
    }


def restore_defaults(args: argparse.Namespace) -> None:
    connection = Connection(args.host, args.port)
    connection.send(
        {
            "cmd": "set",
            "values": {
                "mode": "full_stream",
                "infer_only_runs": DEFAULT_INFER_ONLY_RUNS,
            },
        }
    )
    wait_for_mode(connection, Pipeline(), "full_stream")
    connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--room-temp-c",
        required=True,
        help=(
            "measured room temperature in C, or 'not_measured'"
            " (recorded as such; not a valid baseline)"
        ),
    )
    parser.add_argument(
        "--scene",
        required=True,
        help="e.g. static_silhouettes, walking_video, live_volunteers",
    )
    parser.add_argument("--heatsink", choices=["yes", "no"], required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument(
        "--warmup-s", type=float, default=15, help="after each mode switch"
    )
    parser.add_argument(
        "--initial-warmup-s",
        type=float,
        default=120,
        help="full_stream before the first trial",
    )
    parser.add_argument("--modes", default=",".join(protocol.MODES))
    parser.add_argument("--out", type=Path, default=BASELINES_DIR)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    if args.room_temp_c != "not_measured":
        args.room_temp_c = float(args.room_temp_c)
    return args


def main() -> int:
    args = parse_args()
    modes = args.modes.split(",")
    hello = initial_warmup(args)
    if not hello:
        sys.exit("no HELLO from the device")
    started = datetime.now().strftime("%Y%m%d_%H%M")
    out = args.out / f"{started}_{hello.get('device_id')}_{args.scene}"
    out.mkdir(parents=True)
    (out / "baseline.json").write_text(
        json.dumps(provenance(args, hello, modes), indent=1)
    )
    if str(hello.get("fw_version", "")).endswith("-dirty"):
        print(
            "WARNING: firmware was built from uncommitted changes (-dirty):"
            " not a valid baseline",
            flush=True,
        )

    with open(out / "summary.csv", "w", newline="") as summary_file:
        writer = csv.DictWriter(summary_file, SUMMARY_COLUMNS)
        writer.writeheader()
        for repeat in range(1, args.repeats + 1):
            for mode in modes:
                print(
                    f"trial {mode} r{repeat}: {args.warmup_s:.0f} s warm-up"
                    f" + {args.seconds:.0f} s",
                    flush=True,
                )
                trial = Trial(args, out, mode, repeat)
                row = trial.run()
                writer.writerow(row)
                summary_file.flush()
                print(
                    f"  fps processed {row['fps_processed_mean']}"
                    f" sent {row['fps_sent_mean']}"
                    f" capture {row['capture_fps']}"
                    f"  t_proc {row['t_proc_mean_us']} us"
                    f"  temp {row['temp_end_c']} C",
                    flush=True,
                )
    restore_defaults(args)
    print(f"done: {out}")
    return 0
