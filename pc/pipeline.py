"""Record pipeline shared by the live viewer and replay: frame_id join, stats, tracker, logging.

Join rule: JPEG_FRAME and DETECTIONS are matched by frame_id only, never by arrival order, so
DETECTIONS[n+1] can never be drawn on frame n. In full_stream a frame completes when both halves
are present; in other modes DETECTIONS alone complete it. When a frame completes, any older
incomplete frame is counted as a missing pair and discarded.
"""
import time
from collections import deque

import protocol as P
from clock_sync import ClockSync
from heatmap import MovementGrid, bottom_center
from tracker import Tracker


class Pipeline:
    def __init__(self, logger=None, tracker=None):
        self.logger = logger
        self.tracker = tracker or Tracker()
        self.movement = None
        self.clock = ClockSync()
        self.hello = None
        self.grid = None
        self.metrics = None
        self.events = deque(maxlen=6)
        self.latest = None  # last completed frame
        self.pending = {}
        self.last_seq = None
        self.last_fid = None
        self.discarded = set()  # frame_ids counted as missing pairs (not also counted as gaps)
        self.last_t_s = None
        self.stats = dict(records=0, bytes=0, seq_gaps=0, missing_pairs=0, pairs=0, det_only=0, frame_id_gaps=0)
        self._rate = (time.monotonic(), 0, 0)  # t, bytes, completed frames at last host_stats

    @property
    def mode(self):
        return (self.hello or {}).get("config", {}).get("mode")

    def new_connection(self):
        """seq restarts per connection; half-received frames from the old connection are dropped."""
        self.last_seq = None
        self.pending.clear()

    def reset_movement(self):
        self.tracker.reset()
        if self.movement:
            self.movement.reset()
        self.last_t_s = None

    def feed(self, r, nbytes=0):
        s = self.stats
        s["records"] += 1
        s["bytes"] += nbytes
        if self.last_seq is not None and r.seq != (self.last_seq + 1) & 0xFFFFFFFF:
            s["seq_gaps"] += 1
        self.last_seq = r.seq
        if self.logger:
            self.logger.record(r)

        if r.type == P.HELLO:
            self.hello = P.decode_json(r.payload)
        elif r.type == P.JPEG_FRAME:
            self.pending.setdefault(r.frame_id, {})["jpeg"] = P.decode_jpeg_frame(r.payload)
            self._try_complete(r.frame_id)
        elif r.type == P.DETECTIONS:
            e = self.pending.setdefault(r.frame_id, {})
            e["det"] = P.decode_detections(r.payload)
            e["t_dev_us"] = r.t_dev_us
            self._try_complete(r.frame_id)
        elif r.type == P.PRESENCE_GRID:
            self.grid = P.decode_grid(r.payload)
        elif r.type == P.METRICS:
            self.metrics = P.flatten(P.decode_json(r.payload))
        elif r.type == P.EVENT:
            e = P.decode_json(r.payload)
            self.events.append(f'{e["severity"]}: {e["code"]} {e["text"]}')
        elif r.type == P.PONG:
            self.clock.on_pong(P.decode_json(r.payload))

    def _try_complete(self, fid):
        e = self.pending[fid]
        if "det" not in e or ("jpeg" not in e and self.mode == "full_stream"):
            return
        del self.pending[fid]
        for old in [f for f in self.pending if f < fid]:
            del self.pending[old]
            self.discarded.add(old)
            self.stats["missing_pairs"] += 1
        self._process(fid, e)

    def _process(self, fid, e):
        det, jpeg = e["det"], e.get("jpeg")
        self.stats["pairs" if jpeg else "det_only"] += 1
        if self.last_fid is not None and fid > self.last_fid + 1:
            # frame_ids never seen at all = frames the device dropped (drop-oldest) or lost in transit
            skipped = set(range(self.last_fid + 1, fid))
            self.stats["frame_id_gaps"] += len(skipped - self.discarded)
        self.discarded = {f for f in self.discarded if f > fid}
        self.last_fid = fid
        t_us = jpeg["t_capture_us"] if jpeg else e["t_dev_us"]
        self.latest = {"frame_id": fid, "w": det["w"], "h": det["h"], "boxes": det["boxes"],
                       "jpeg": jpeg["jpeg"] if jpeg and jpeg["jpeg"] else None,
                       "t_capture_us": jpeg["t_capture_us"] if jpeg else None}

        if self.movement is None or (self.movement.w, self.movement.h) != (det["w"], det["h"]):
            self.movement = MovementGrid(det["w"], det["h"])  # coordinates changed: start over
            self.tracker.reset()
            self.last_t_s = None
        t_s = t_us / 1e6
        if self.last_t_s is not None and 0 < t_s - self.last_t_s < 5:  # gaps > 5 s are not "observed"
            self.movement.observed_s += t_s - self.last_t_s
        self.last_t_s = t_s
        for p0, p1 in self.tracker.update([bottom_center(b) for b in det["boxes"]], t_s):
            self.movement.add_segment(p0, p1)

    def host_stats(self, parser, extra=None):
        """Per-second host stats row (and logs it). parser: the protocol.Parser feeding this pipeline."""
        now = time.monotonic()
        t0, b0, f0 = self._rate
        done = self.stats["pairs"] + self.stats["det_only"]
        dt = max(now - t0, 1e-6)
        self._rate = (now, self.stats["bytes"], done)
        best = self.clock.best()
        row = {"rx_records": self.stats["records"], "rx_bytes_per_s": round((self.stats["bytes"] - b0) / dt),
               "rx_pairs_per_s": round((done - f0) / dt, 2), "seq_gaps": self.stats["seq_gaps"],
               "crc_errors": parser.crc_errors, "resyncs": parser.resyncs, "oversize": parser.oversize,
               "missing_pairs": self.stats["missing_pairs"],
               "clock_offset_us": round(best[0]) if best else "", "clock_uncertainty_us": round(best[1]) if best else ""}
        row.update(extra or {})
        if self.logger:
            self.logger.host_stats(row)
            self.logger.flush()
        return row
