"""Saves a session to a run folder: runs/<date_time>_<board>/

  hello.json         the board's details and settings (a new entry each time a setting changes)
  detections.jsonl   one line per DETECTIONS record
  metrics.csv        one row per METRICS window, fixed columns (METRICS_COLUMNS)
  events.jsonl       EVENT records
  grid_snapshots.csv PRESENCE_GRID records (counts flattened row-major)
  host_stats.csv     receiver/viewer stats, once per second
  frame_timing.jsonl FRAME_TIMING records (only when metrics_verbose is on)
  stream.gmtp        every record as received. JPEG bytes are kept only while recording is on;
                     otherwise JPEG_FRAME keeps its 16-byte header with an empty image, so seq and
                     frame_id stay continuous for replay.
  frames/            <frame_id>.jpg, only while recording is on (camera images can show people)
"""
import csv
import json
from datetime import datetime
from pathlib import Path

from connection import protocol as P
from analysis.heatmap import COLS, ROWS
from utils.paths import RUNS_DIR

STAGES = ("t_wait_frame", "t_decode", "t_pre", "t_infer", "t_post", "t_grid", "t_copy", "t_enqueue", "t_proc",
          "t_send")
STAGE_STATISTICS = ("n", "min", "mean", "max", "p95")
COUNTERS = ("cap_ok", "cap_timeout", "no_eoi", "no_soi", "fb_ovf", "decode_fail", "oversize", "frames_processed",
            "cadence_skipped", "still_runs", "bundles_sent", "tx_dropped", "no_client_discards", "events_dropped",
            "clients", "cmd_ok", "cmd_err", "rx_crc_errors", "rx_oversize", "wifi_disconnects", "bytes_sent",
            "ready_queue_depth")
METRICS_COLUMNS = (["host_time", "t_dev_us", "mode", "window_us", "fps_processed", "fps_sent", "bytes_per_s",
                    "dropped_in_window"]
                   + [f"stages.{stage}.{statistic}" for stage in STAGES for statistic in STAGE_STATISTICS]
                   + [f"counters.{counter}" for counter in COUNTERS]
                   + ["mem.int_free", "mem.int_min", "mem.ps_free", "mem.ps_min", "temp_c", "rssi", "wifi",
                      "wifi_reconnects", "uptime_s", "reset_reason"])
"""Fixed so the CSV keeps the same columns even when a window has no samples for some stage."""
HOST_STATS_COLUMNS = ["host_time", "rx_records", "rx_bytes_per_s", "rx_pairs_per_s", "display_fps", "display_ms",
                      "seq_gaps", "crc_errors", "resyncs", "oversize", "missing_pairs", "clock_offset_us",
                      "clock_uncertainty_us", "capture_to_display_ms", "capture_to_display_unc_ms"]
GRID_COLUMNS = (["host_time", "t_dev_us", "frames", "elapsed_us", "frame_w", "frame_h", "cols", "rows"]
                + [f"c{i}" for i in range(COLS * ROWS)])


def now_iso():
    return datetime.now().isoformat(timespec="milliseconds")


class RunLogger:
    """Writes one run folder. The folder is created at the first record, named after the device in HELLO."""

    def __init__(self, root=RUNS_DIR, recording=False):
        self.root = Path(root)
        self.recording = recording
        self.dir = None
        self.hellos = []
        self._files = []
        self._frame_timing = None

    def _open_file(self, name, mode="a", **kwargs):
        handle = open(self.dir / name, mode, **kwargs)
        self._files.append(handle)
        return handle

    def _open_run(self, device_id):
        self.dir = self.root / f"{datetime.now():%Y%m%d_%H%M%S}_{device_id}"
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        self._detections = self._open_file("detections.jsonl")
        self._events = self._open_file("events.jsonl")
        self._stream = self._open_file("stream.gmtp", "ab")
        self._metrics = csv.DictWriter(self._open_file("metrics.csv", newline=""), METRICS_COLUMNS,
                                       extrasaction="ignore")
        self._metrics.writeheader()
        self._grid = csv.writer(self._open_file("grid_snapshots.csv", newline=""))
        self._grid.writerow(GRID_COLUMNS)
        self._host_stats = csv.DictWriter(self._open_file("host_stats.csv", newline=""), HOST_STATS_COLUMNS,
                                          extrasaction="ignore")
        self._host_stats.writeheader()

    def record(self, record):
        """Log one parsed protocol.Record."""
        if record.type == P.HELLO:
            self._log_hello(P.decode_json(record.payload))
        if self.dir is None:
            self._open_run("unknown")
        self._append_to_stream(record)
        if record.type == P.JPEG_FRAME and self.recording:
            jpeg = P.decode_jpeg_frame(record.payload)["jpeg"]
            (self.dir / "frames" / f"{record.frame_id:08d}.jpg").write_bytes(jpeg)
        elif record.type == P.DETECTIONS:
            self._write_json_line(self._detections, {"host_time": now_iso(), "frame_id": record.frame_id,
                                                     "t_dev_us": record.t_dev_us,
                                                     **P.decode_detections(record.payload)})
        elif record.type == P.METRICS:
            self._metrics.writerow({"host_time": now_iso(), "t_dev_us": record.t_dev_us,
                                    **P.flatten(P.decode_json(record.payload))})
        elif record.type == P.EVENT:
            self._write_json_line(self._events, {"host_time": now_iso(), "t_dev_us": record.t_dev_us,
                                                 **P.decode_json(record.payload)})
        elif record.type == P.PRESENCE_GRID:
            grid = P.decode_grid(record.payload)
            self._grid.writerow([now_iso(), record.t_dev_us, grid["frames"], grid["elapsed_us"], grid["frame_w"],
                                 grid["frame_h"], grid["cols"], grid["rows"]]
                                + [count for row in grid["counts"] for count in row])
        elif record.type == P.FRAME_TIMING:
            if self._frame_timing is None:
                self._frame_timing = self._open_file("frame_timing.jsonl")
            self._write_json_line(self._frame_timing, P.decode_json(record.payload))

    def _log_hello(self, hello):
        if self.dir is None:
            self._open_run(hello.get("device_id", "unknown"))
        self.hellos.append({"host_time": now_iso(), **hello})
        (self.dir / "hello.json").write_text(json.dumps(self.hellos, indent=1))

    def _append_to_stream(self, record):
        """Without recording, a JPEG_FRAME keeps only its header: no image ever reaches disk."""
        payload = record.payload
        if record.type == P.JPEG_FRAME and not self.recording:
            payload = payload[:P.JPEG_FRAME_HEADER.size]
        self._stream.write(P.encode(record.type, record.seq, record.frame_id, record.t_dev_us, payload))

    @staticmethod
    def _write_json_line(handle, obj):
        handle.write(json.dumps(obj) + "\n")

    def host_stats(self, row):
        """Append a host_stats.csv row. Skipped until HELLO has named the run folder."""
        if self.dir is not None:
            self._host_stats.writerow({"host_time": now_iso(), **row})

    def flush(self):
        for handle in self._files:
            handle.flush()

    def close(self):
        for handle in self._files:
            handle.close()
