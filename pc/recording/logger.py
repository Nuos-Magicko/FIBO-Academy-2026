"""Saves a session to a run folder: runs/<date_time>_<board>/

hello.json         the board's details and settings (a new entry each time a
                   setting changes)
detections.jsonl   one line per DETECTIONS record
metrics.csv        one row per METRICS window, fixed columns (METRICS_COLUMNS)
events.jsonl       EVENT records
grid_snapshots.csv PRESENCE_GRID records (counts flattened row-major)
host_stats.csv     receiver/viewer stats, once per second
frame_timing.jsonl FRAME_TIMING records (only when metrics_verbose is on)
stream.gmtp        every record as received. JPEG bytes are kept only while
                   recording is on; otherwise JPEG_FRAME keeps its 16-byte
                   header with an empty image, so seq and
                   frame_id stay continuous for replay.
frames/            <frame_id>.jpg, only while recording is on (camera images
                   can show people)
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from connection import protocol
from analysis.heatmap import COLS, ROWS
from utils.paths import RUNS_DIR

STAGES = (
    "t_wait_frame",
    "t_decode",
    "t_pre",
    "t_infer",
    "t_post",
    "t_grid",
    "t_copy",
    "t_enqueue",
    "t_proc",
    "t_send",
)
STAGE_STATISTICS = ("n", "min", "mean", "max", "p95")
COUNTERS = (
    "cap_ok",
    "cap_timeout",
    "no_eoi",
    "no_soi",
    "fb_ovf",
    "decode_fail",
    "oversize",
    "frames_processed",
    "cadence_skipped",
    "still_runs",
    "bundles_sent",
    "tx_dropped",
    "no_client_discards",
    "events_dropped",
    "clients",
    "cmd_ok",
    "cmd_err",
    "rx_crc_errors",
    "rx_oversize",
    "wifi_disconnects",
    "bytes_sent",
    "ready_queue_depth",
)


def _metrics_columns() -> list[str]:
    columns = [
        "host_time",
        "t_dev_us",
        "mode",
        "window_us",
        "fps_processed",
        "fps_sent",
        "bytes_per_s",
        "dropped_in_window",
    ]
    for stage in STAGES:
        for statistic in STAGE_STATISTICS:
            columns.append(f"stages.{stage}.{statistic}")
    for counter in COUNTERS:
        columns.append(f"counters.{counter}")
    columns += [
        "mem.int_free",
        "mem.int_min",
        "mem.ps_free",
        "mem.ps_min",
        "temp_c",
        "rssi",
        "wifi",
        "wifi_reconnects",
        "uptime_s",
        "reset_reason",
    ]
    return columns


METRICS_COLUMNS = _metrics_columns()
"""Fixed so the CSV keeps the same columns even when a window has no samples
for some stage."""
HOST_STATS_COLUMNS = [
    "host_time",
    "rx_records",
    "rx_bytes_per_s",
    "rx_pairs_per_s",
    "display_fps",
    "display_ms",
    "seq_gaps",
    "crc_errors",
    "resyncs",
    "oversize",
    "missing_pairs",
    "clock_offset_us",
    "clock_uncertainty_us",
    "capture_to_display_ms",
    "capture_to_display_unc_ms",
]
GRID_COLUMNS = [
    "host_time",
    "t_dev_us",
    "frames",
    "elapsed_us",
    "frame_w",
    "frame_h",
    "cols",
    "rows",
]
for cell in range(COLS * ROWS):
    GRID_COLUMNS.append(f"c{cell}")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class RunLogger:
    """Writes one run folder. The folder is created at the first record, named
    after the device in HELLO."""

    def __init__(
        self, root: Path | str = RUNS_DIR, recording: bool = False
    ) -> None:
        self.root = Path(root)
        self.recording = recording
        self.dir: Path | None = None
        self.hellos: list[dict[str, Any]] = []
        self._files: list[IO[Any]] = []
        self._frame_timing: IO[Any] | None = None

    def _run_dir(self) -> Path:
        """The run folder; it exists once the first record has arrived."""
        if self.dir is None:
            raise RuntimeError("the run folder is not open yet")
        return self.dir

    def _open_file(
        self, name: str, mode: str = "a", newline: str | None = None
    ) -> IO[Any]:
        handle = open(self._run_dir() / name, mode, newline=newline)
        self._files.append(handle)
        return handle

    def _open_run(self, device_id: str) -> None:
        started = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.root / f"{started}_{device_id}"
        (run_dir / "frames").mkdir(parents=True, exist_ok=True)
        self.dir = run_dir
        self._detections = self._open_file("detections.jsonl")
        self._events = self._open_file("events.jsonl")
        self._stream = self._open_file("stream.gmtp", "ab")
        self._metrics = csv.DictWriter(
            self._open_file("metrics.csv", newline=""),
            METRICS_COLUMNS,
            extrasaction="ignore",
        )
        self._metrics.writeheader()
        self._grid = csv.writer(
            self._open_file("grid_snapshots.csv", newline="")
        )
        self._grid.writerow(GRID_COLUMNS)
        self._host_stats = csv.DictWriter(
            self._open_file("host_stats.csv", newline=""),
            HOST_STATS_COLUMNS,
            extrasaction="ignore",
        )
        self._host_stats.writeheader()

    def record(self, record: protocol.Record) -> None:
        """Log one parsed protocol.Record."""
        if record.type == protocol.HELLO:
            self._log_hello(protocol.decode_json(record.payload))
        if self.dir is None:
            self._open_run("unknown")
        self._append_to_stream(record)

        if record.type == protocol.JPEG_FRAME and self.recording:
            jpeg = protocol.decode_jpeg_frame(record.payload)["jpeg"]
            image_name = f"{record.frame_id:08d}.jpg"
            (self._run_dir() / "frames" / image_name).write_bytes(jpeg)
        elif record.type == protocol.DETECTIONS:
            line: dict[str, Any] = {
                "host_time": now_iso(),
                "frame_id": record.frame_id,
                "t_dev_us": record.t_dev_us,
            }
            line.update(protocol.decode_detections(record.payload))
            self._write_json_line(self._detections, line)
        elif record.type == protocol.METRICS:
            row: dict[str, Any] = {
                "host_time": now_iso(),
                "t_dev_us": record.t_dev_us,
            }
            row.update(protocol.flatten(protocol.decode_json(record.payload)))
            self._metrics.writerow(row)
        elif record.type == protocol.EVENT:
            event = {"host_time": now_iso(), "t_dev_us": record.t_dev_us}
            event.update(protocol.decode_json(record.payload))
            self._write_json_line(self._events, event)
        elif record.type == protocol.PRESENCE_GRID:
            grid = protocol.decode_grid(record.payload)
            grid_row = [
                now_iso(),
                record.t_dev_us,
                grid["frames"],
                grid["elapsed_us"],
                grid["frame_w"],
                grid["frame_h"],
                grid["cols"],
                grid["rows"],
            ]
            for counts_in_row in grid["counts"]:
                grid_row += counts_in_row
            self._grid.writerow(grid_row)
        elif record.type == protocol.FRAME_TIMING:
            if self._frame_timing is None:
                self._frame_timing = self._open_file("frame_timing.jsonl")
            timing = protocol.decode_json(record.payload)
            self._write_json_line(self._frame_timing, timing)

    def _log_hello(self, hello: dict[str, Any]) -> None:
        if self.dir is None:
            self._open_run(hello.get("device_id", "unknown"))
        entry: dict[str, Any] = {"host_time": now_iso()}
        entry.update(hello)
        self.hellos.append(entry)
        hello_text = json.dumps(self.hellos, indent=1)
        (self._run_dir() / "hello.json").write_text(hello_text)

    def _append_to_stream(self, record: protocol.Record) -> None:
        """Without recording, a JPEG_FRAME keeps only its header: no image ever
        reaches disk."""
        payload = record.payload
        if record.type == protocol.JPEG_FRAME and not self.recording:
            payload = payload[: protocol.JPEG_FRAME_HEADER.size]
        self._stream.write(
            protocol.encode(
                record.type,
                record.seq,
                record.frame_id,
                record.t_dev_us,
                payload,
            )
        )

    @staticmethod
    def _write_json_line(handle: IO[Any], obj: dict[str, Any]) -> None:
        handle.write(json.dumps(obj) + "\n")

    def host_stats(self, row: dict[str, Any]) -> None:
        """Append a host_stats.csv row. Skipped until HELLO has named the run
        folder."""
        if self.dir is not None:
            line = {"host_time": now_iso()}
            line.update(row)
            self._host_stats.writerow(line)

    def flush(self) -> None:
        for handle in self._files:
            handle.flush()

    def close(self) -> None:
        for handle in self._files:
            handle.close()
