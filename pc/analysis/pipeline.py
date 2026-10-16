"""What the laptop does with every message from the board (used by both the viewer and replay).

The board sends each frame as two messages: the picture (JPEG_FRAME) and its boxes
(DETECTIONS). Both carry the same frame_id number. This file puts the two halves back together
by matching that number, never by the order they arrive in, so the boxes of frame 101 can never
be drawn on picture 100.

It also:
  - counts anything lost: missing sequence numbers (seq_gaps), frames where one half never
    arrived (missing_pairs), and frame numbers that were skipped (frame_id_gaps, usually frames
    the board dropped because its queue was full);
  - feeds the people's positions to the tracker for the movement heatmap;
  - passes every message to the logger, which writes the run folder.

In metadata_stream and inference_only there are no pictures, so the boxes alone complete a frame.
"""
import time
from collections import deque

from connection import protocol as P
from connection.clock_sync import ClockSync
from analysis.heatmap import MovementGrid, bottom_center
from analysis.tracker import Tracker

UINT32_MASK = 0xFFFFFFFF
MAX_OBSERVED_GAP_S = 5
"""A gap between frames longer than this does not count as observed time for the movement map."""
RECENT_EVENTS = 6


class Pipeline:
    """Consumes protocol.Records and keeps everything the viewer and the logs need.

    Attributes:
        latest: the last completed frame {frame_id, w, h, boxes, jpeg, t_capture_us}, or None.
        stats: records, bytes, seq_gaps, missing_pairs, pairs, det_only and frame_id_gaps.
            frame_id_gaps counts frame ids never seen at all: frames the device dropped
            (drop-oldest) or lost in transit, not frames counted as missing pairs.
    """

    def __init__(self, logger=None, tracker=None):
        self.logger = logger
        self.tracker = tracker or Tracker()
        self.movement = None
        self.clock = ClockSync()
        self.hello = None
        self.grid = None
        self.metrics = None
        self.events = deque(maxlen=RECENT_EVENTS)
        self.latest = None
        self.stats = dict(records=0, bytes=0, seq_gaps=0, missing_pairs=0, pairs=0, det_only=0, frame_id_gaps=0)
        self._pending_frames = {}
        self._incomplete_frame_ids = set()
        self._last_seq = None
        self._last_frame_id = None
        self._last_frame_t_s = None
        self._at_last_host_stats = (time.monotonic(), 0, 0)

    @property
    def mode(self):
        return (self.hello or {}).get("config", {}).get("mode")

    def new_connection(self):
        """seq restarts per connection; half-received frames from the old connection are dropped."""
        self._last_seq = None
        self._pending_frames.clear()

    def reset_movement(self):
        self.tracker.reset()
        if self.movement:
            self.movement.reset()
        self._last_frame_t_s = None

    def feed(self, record, nbytes=0):
        """Process one record; nbytes is what it cost on the wire (0 if counted elsewhere)."""
        self.stats["records"] += 1
        self.stats["bytes"] += nbytes
        self._check_seq(record.seq)
        if self.logger:
            self.logger.record(record)
        if record.type == P.HELLO:
            self.hello = P.decode_json(record.payload)
        elif record.type == P.JPEG_FRAME:
            self._pending_frames.setdefault(record.frame_id, {})["jpeg"] = P.decode_jpeg_frame(record.payload)
            self._try_complete(record.frame_id)
        elif record.type == P.DETECTIONS:
            frame = self._pending_frames.setdefault(record.frame_id, {})
            frame["detections"] = P.decode_detections(record.payload)
            frame["t_dev_us"] = record.t_dev_us
            self._try_complete(record.frame_id)
        elif record.type == P.PRESENCE_GRID:
            self.grid = P.decode_grid(record.payload)
        elif record.type == P.METRICS:
            self.metrics = P.flatten(P.decode_json(record.payload))
        elif record.type == P.EVENT:
            event = P.decode_json(record.payload)
            self.events.append(f'{event["severity"]}: {event["code"]} {event["text"]}')
        elif record.type == P.PONG:
            self.clock.on_pong(P.decode_json(record.payload))

    def _check_seq(self, seq):
        if self._last_seq is not None and seq != (self._last_seq + 1) & UINT32_MASK:
            self.stats["seq_gaps"] += 1
        self._last_seq = seq

    def _try_complete(self, frame_id):
        frame = self._pending_frames[frame_id]
        waiting_for_jpeg = "jpeg" not in frame and self.mode == "full_stream"
        if "detections" not in frame or waiting_for_jpeg:
            return
        del self._pending_frames[frame_id]
        for older in [pending for pending in self._pending_frames if pending < frame_id]:
            del self._pending_frames[older]
            self._incomplete_frame_ids.add(older)
            self.stats["missing_pairs"] += 1
        self._complete(frame_id, frame)

    def _count_frame_id_gap(self, frame_id):
        if self._last_frame_id is not None and frame_id > self._last_frame_id + 1:
            never_seen = set(range(self._last_frame_id + 1, frame_id)) - self._incomplete_frame_ids
            self.stats["frame_id_gaps"] += len(never_seen)
        self._incomplete_frame_ids = {f for f in self._incomplete_frame_ids if f > frame_id}
        self._last_frame_id = frame_id

    def _complete(self, frame_id, frame):
        detections, jpeg = frame["detections"], frame.get("jpeg")
        self.stats["pairs" if jpeg else "det_only"] += 1
        self._count_frame_id_gap(frame_id)
        self.latest = {"frame_id": frame_id, "w": detections["w"], "h": detections["h"],
                       "boxes": detections["boxes"],
                       "jpeg": jpeg["jpeg"] if jpeg and jpeg["jpeg"] else None,
                       "t_capture_us": jpeg["t_capture_us"] if jpeg else None}
        t_s = (jpeg["t_capture_us"] if jpeg else frame["t_dev_us"]) / 1e6
        self._update_movement(detections, t_s)

    def _update_movement(self, detections, t_s):
        """Coordinates change with the frame size, so a new size starts a new movement map."""
        size = (detections["w"], detections["h"])
        if self.movement is None or (self.movement.w, self.movement.h) != size:
            self.movement = MovementGrid(*size)
            self.tracker.reset()
            self._last_frame_t_s = None
        if self._last_frame_t_s is not None and 0 < t_s - self._last_frame_t_s < MAX_OBSERVED_GAP_S:
            self.movement.observed_s += t_s - self._last_frame_t_s
        self._last_frame_t_s = t_s
        for start, end in self.tracker.update([bottom_center(box) for box in detections["boxes"]], t_s):
            self.movement.add_segment(start, end)

    def host_stats(self, parser, extra=None):
        """Build (and log) one host_stats row covering the time since the previous call.

        parser is the protocol.Parser feeding this pipeline; extra adds viewer-side columns.
        """
        now = time.monotonic()
        then, bytes_then, frames_then = self._at_last_host_stats
        frames_now = self.stats["pairs"] + self.stats["det_only"]
        elapsed = max(now - then, 1e-6)
        self._at_last_host_stats = (now, self.stats["bytes"], frames_now)
        clock = self.clock.best()
        row = {"rx_records": self.stats["records"],
               "rx_bytes_per_s": round((self.stats["bytes"] - bytes_then) / elapsed),
               "rx_pairs_per_s": round((frames_now - frames_then) / elapsed, 2),
               "seq_gaps": self.stats["seq_gaps"], "crc_errors": parser.crc_errors, "resyncs": parser.resyncs,
               "oversize": parser.oversize, "missing_pairs": self.stats["missing_pairs"],
               "clock_offset_us": round(clock[0]) if clock else "",
               "clock_uncertainty_us": round(clock[1]) if clock else ""}
        row.update(extra or {})
        if self.logger:
            self.logger.host_stats(row)
            self.logger.flush()
        return row
