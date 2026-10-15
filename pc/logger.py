"""Run logger: runs/<timestamp>_<device_id>/

  hello.json         every HELLO received, in order ([0] = device facts, [-1] = current config)
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

import protocol as P
from heatmap import COLS, ROWS

STAGES = ("t_wait_frame", "t_decode", "t_pre", "t_infer", "t_post", "t_grid", "t_copy", "t_enqueue", "t_proc",
          "t_send")
STATS = ("n", "min", "mean", "max", "p95")
COUNTERS = ("cap_ok", "cap_timeout", "no_eoi", "no_soi", "fb_ovf", "decode_fail", "oversize", "frames_processed",
            "cadence_skipped", "still_runs", "bundles_sent", "tx_dropped", "no_client_discards", "events_dropped",
            "clients", "cmd_ok", "cmd_err", "rx_crc_errors", "rx_oversize", "wifi_disconnects", "bytes_sent",
            "ready_queue_depth")
METRICS_COLUMNS = (["host_time", "t_dev_us", "mode", "window_us", "fps_processed", "fps_sent", "bytes_per_s",
                    "dropped_in_window"]
                   + [f"stages.{s}.{k}" for s in STAGES for k in STATS]
                   + [f"counters.{c}" for c in COUNTERS]
                   + ["mem.int_free", "mem.int_min", "mem.ps_free", "mem.ps_min", "temp_c", "rssi", "wifi",
                      "wifi_reconnects", "uptime_s", "reset_reason"])
HOST_STATS_COLUMNS = ["host_time", "rx_records", "rx_bytes_per_s", "rx_pairs_per_s", "display_fps", "display_ms",
                      "seq_gaps", "crc_errors", "resyncs", "oversize", "missing_pairs", "clock_offset_us",
                      "clock_uncertainty_us", "capture_to_display_ms", "capture_to_display_unc_ms"]


def now_iso():
    return datetime.now().isoformat(timespec="milliseconds")


class RunLogger:
    def __init__(self, root="runs", recording=False):
        self.root = Path(root)
        self.recording = recording
        self.dir = None
        self.hellos = []

    def _open(self, device_id):
        self.dir = self.root / f"{datetime.now():%Y%m%d_%H%M%S}_{device_id}"
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        self.f_det = open(self.dir / "detections.jsonl", "a")
        self.f_ev = open(self.dir / "events.jsonl", "a")
        self.f_ft = None
        self.f_stream = open(self.dir / "stream.gmtp", "ab")
        self.f_met = open(self.dir / "metrics.csv", "a", newline="")
        self.w_met = csv.DictWriter(self.f_met, METRICS_COLUMNS, extrasaction="ignore")
        self.w_met.writeheader()
        self.f_grid = open(self.dir / "grid_snapshots.csv", "a", newline="")
        self.w_grid = csv.writer(self.f_grid)
        self.w_grid.writerow(["host_time", "t_dev_us", "frames", "elapsed_us", "frame_w", "frame_h", "cols", "rows"]
                             + [f"c{i}" for i in range(COLS * ROWS)])
        self.f_host = open(self.dir / "host_stats.csv", "a", newline="")
        self.w_host = csv.DictWriter(self.f_host, HOST_STATS_COLUMNS, extrasaction="ignore")
        self.w_host.writeheader()

    def _ensure(self):
        if self.dir is None:
            self._open("unknown")

    def record(self, r):
        """Log one parsed protocol.Record."""
        if r.type == P.HELLO:
            hello = P.decode_json(r.payload)
            if self.dir is None:
                self._open(hello.get("device_id", "unknown"))
            self.hellos.append({"host_time": now_iso(), **hello})
            (self.dir / "hello.json").write_text(json.dumps(self.hellos, indent=1))
        self._ensure()
        payload = r.payload
        if r.type == P.JPEG_FRAME and not self.recording:
            payload = payload[:P.JPEG_HDR.size]
        self.f_stream.write(P.encode(r.type, r.seq, r.frame_id, r.t_dev_us, payload))
        if r.type == P.JPEG_FRAME and self.recording:
            (self.dir / "frames" / f"{r.frame_id:08d}.jpg").write_bytes(P.decode_jpeg_frame(r.payload)["jpeg"])
        elif r.type == P.DETECTIONS:
            d = P.decode_detections(r.payload)
            self.f_det.write(json.dumps({"host_time": now_iso(), "frame_id": r.frame_id, "t_dev_us": r.t_dev_us,
                                         **d}) + "\n")
        elif r.type == P.METRICS:
            m = P.flatten(P.decode_json(r.payload))
            self.w_met.writerow({"host_time": now_iso(), "t_dev_us": r.t_dev_us, **m})
        elif r.type == P.EVENT:
            self.f_ev.write(json.dumps({"host_time": now_iso(), "t_dev_us": r.t_dev_us,
                                        **P.decode_json(r.payload)}) + "\n")
        elif r.type == P.PRESENCE_GRID:
            g = P.decode_grid(r.payload)
            self.w_grid.writerow([now_iso(), r.t_dev_us, g["frames"], g["elapsed_us"], g["frame_w"], g["frame_h"],
                                  g["cols"], g["rows"]] + [v for row in g["counts"] for v in row])
        elif r.type == P.FRAME_TIMING:
            if self.f_ft is None:
                self.f_ft = open(self.dir / "frame_timing.jsonl", "a")
            self.f_ft.write(json.dumps(P.decode_json(r.payload)) + "\n")

    def host_stats(self, row):
        if self.dir is None:  # nothing received yet: wait for HELLO so the run folder gets the device id
            return
        self.w_host.writerow({"host_time": now_iso(), **row})

    def flush(self):
        if self.dir is None:
            return
        for f in (self.f_det, self.f_ev, self.f_stream, self.f_met, self.f_grid, self.f_host, self.f_ft):
            if f:
                f.flush()

    def close(self):
        self.flush()
        if self.dir is None:
            return
        for f in (self.f_det, self.f_ev, self.f_stream, self.f_met, self.f_grid, self.f_host, self.f_ft):
            if f:
                f.close()
