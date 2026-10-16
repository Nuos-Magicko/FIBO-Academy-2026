"""What the laptop does with every message from the board (used by both the
viewer and replay).

The board sends each frame as two messages: the picture (JPEG_FRAME) and its
boxes (DETECTIONS). Both carry the same frame_id number. This file puts the two
halves back together by matching that number, never by the order they arrive
in, so the boxes of frame 101 can never be drawn on picture 100.

It also:
  - counts anything lost: missing sequence numbers (seq_gaps), frames where
    one half never arrived (missing_pairs), and frame numbers that were
    skipped (frame_id_gaps, usually frames the board dropped because its
    queue was full);
  - feeds the people's positions to the tracker for the movement heatmap;
  - passes every message to the logger, which writes the run folder.

In metadata_stream and inference_only there are no pictures, so the boxes alone
complete a frame.
"""

import time
from collections import deque
from typing import Any

from connection import protocol
from connection.clock_sync import ClockSync
from analysis.heatmap import MovementGrid, bottom_center
from analysis.tracker import Tracker
from recording.logger import RunLogger

UINT32_MASK = 0xFFFFFFFF
MAX_OBSERVED_GAP_S = 5
"""A gap between frames longer than this does not count as observed time for
the movement map."""
RECENT_EVENTS = 6


class Pipeline:
    """Consumes protocol.Records and keeps everything the viewer and the logs
    need.

    Attributes:
        latest: the last completed frame
            {frame_id, w, h, boxes, jpeg, t_capture_us}, or None.
        stats: records, bytes, seq_gaps, missing_pairs, pairs, det_only and
            frame_id_gaps. frame_id_gaps counts frame ids never seen at all:
            frames the device dropped (drop-oldest) or lost in transit, not
            frames counted as missing pairs.
    """

    def __init__(
        self, logger: RunLogger | None = None, tracker: Tracker | None = None
    ) -> None:
        self.logger = logger
        if tracker is None:
            tracker = Tracker()
        self.tracker = tracker
        self.movement: MovementGrid | None = None
        self.clock = ClockSync()
        self.hello: dict[str, Any] | None = None
        self.grid: dict[str, Any] | None = None
        self.metrics: dict[str, Any] | None = None
        self.events: deque[str] = deque(maxlen=RECENT_EVENTS)
        self.latest: dict[str, Any] | None = None
        self.stats = {
            "records": 0,
            "bytes": 0,
            "seq_gaps": 0,
            "missing_pairs": 0,
            "pairs": 0,
            "det_only": 0,
            "frame_id_gaps": 0,
        }
        self._pending_frames: dict[int, dict[str, Any]] = {}
        self._incomplete_frame_ids: set[int] = set()
        self._last_seq: int | None = None
        self._last_frame_id: int | None = None
        self._last_frame_t_s: float | None = None
        self._at_last_host_stats = (time.monotonic(), 0, 0)

    @property
    def mode(self) -> str | None:
        if self.hello is None:
            return None
        config = self.hello.get("config", {})
        return config.get("mode")

    def new_connection(self) -> None:
        """seq restarts per connection; half-received frames from the old
        connection are dropped."""
        self._last_seq = None
        self._pending_frames.clear()

    def reset_movement(self) -> None:
        self.tracker.reset()
        if self.movement is not None:
            self.movement.reset()
        self._last_frame_t_s = None

    def feed(self, record: protocol.Record, nbytes: int = 0) -> None:
        """Process one record; nbytes is what it cost on the wire (0 if counted
        elsewhere)."""
        self.stats["records"] += 1
        self.stats["bytes"] += nbytes
        self._check_seq(record.seq)
        if self.logger is not None:
            self.logger.record(record)

        if record.type == protocol.HELLO:
            self.hello = protocol.decode_json(record.payload)
        elif record.type == protocol.JPEG_FRAME:
            frame = self._pending_frame(record.frame_id)
            frame["jpeg"] = protocol.decode_jpeg_frame(record.payload)
            self._try_complete(record.frame_id)
        elif record.type == protocol.DETECTIONS:
            frame = self._pending_frame(record.frame_id)
            frame["detections"] = protocol.decode_detections(record.payload)
            frame["t_dev_us"] = record.t_dev_us
            self._try_complete(record.frame_id)
        elif record.type == protocol.PRESENCE_GRID:
            self.grid = protocol.decode_grid(record.payload)
        elif record.type == protocol.METRICS:
            self.metrics = protocol.flatten(
                protocol.decode_json(record.payload)
            )
        elif record.type == protocol.EVENT:
            event = protocol.decode_json(record.payload)
            self.events.append(
                f'{event["severity"]}: {event["code"]} {event["text"]}'
            )
        elif record.type == protocol.PONG:
            self.clock.on_pong(protocol.decode_json(record.payload))

    def _pending_frame(self, frame_id: int) -> dict[str, Any]:
        """The half-received frame with this frame_id, created empty the first
        time."""
        if frame_id not in self._pending_frames:
            self._pending_frames[frame_id] = {}
        return self._pending_frames[frame_id]

    def _check_seq(self, seq: int) -> None:
        if self._last_seq is not None:
            expected = (self._last_seq + 1) & UINT32_MASK
            if seq != expected:
                self.stats["seq_gaps"] += 1
        self._last_seq = seq

    def _try_complete(self, frame_id: int) -> None:
        frame = self._pending_frames[frame_id]
        if "detections" not in frame:
            return
        if "jpeg" not in frame and self.mode == "full_stream":
            return
        del self._pending_frames[frame_id]

        older_frame_ids = []
        for pending_id in self._pending_frames:
            if pending_id < frame_id:
                older_frame_ids.append(pending_id)
        for older in older_frame_ids:
            del self._pending_frames[older]
            self._incomplete_frame_ids.add(older)
            self.stats["missing_pairs"] += 1
        self._complete(frame_id, frame)

    def _count_frame_id_gap(self, frame_id: int) -> None:
        if (
            self._last_frame_id is not None
            and frame_id > self._last_frame_id + 1
        ):
            skipped = set(range(self._last_frame_id + 1, frame_id))
            never_seen = skipped - self._incomplete_frame_ids
            self.stats["frame_id_gaps"] += len(never_seen)

        still_ahead = set()
        for incomplete_id in self._incomplete_frame_ids:
            if incomplete_id > frame_id:
                still_ahead.add(incomplete_id)
        self._incomplete_frame_ids = still_ahead
        self._last_frame_id = frame_id

    def _complete(self, frame_id: int, frame: dict[str, Any]) -> None:
        detections = frame["detections"]
        jpeg = frame.get("jpeg")
        if jpeg:
            self.stats["pairs"] += 1
        else:
            self.stats["det_only"] += 1
        self._count_frame_id_gap(frame_id)

        jpeg_bytes = None
        t_capture_us = None
        if jpeg:
            t_capture_us = jpeg["t_capture_us"]
            if jpeg["jpeg"]:
                jpeg_bytes = jpeg["jpeg"]
        self.latest = {
            "frame_id": frame_id,
            "w": detections["w"],
            "h": detections["h"],
            "boxes": detections["boxes"],
            "jpeg": jpeg_bytes,
            "t_capture_us": t_capture_us,
        }

        if jpeg:
            t_s = jpeg["t_capture_us"] / 1e6
        else:
            t_s = frame["t_dev_us"] / 1e6
        self._update_movement(detections, t_s)

    def _update_movement(self, detections: dict[str, Any], t_s: float) -> None:
        """Coordinates change with the frame size, so a new size starts a new
        movement map."""
        width = detections["w"]
        height = detections["h"]
        movement = self.movement
        size_changed = False
        if movement is None:
            size_changed = True
        elif movement.width != width or movement.height != height:
            size_changed = True
        if movement is None or size_changed:
            movement = MovementGrid(width, height)
            self.movement = movement
            self.tracker.reset()
            self._last_frame_t_s = None

        if self._last_frame_t_s is not None:
            gap_s = t_s - self._last_frame_t_s
            if gap_s > 0 and gap_s < MAX_OBSERVED_GAP_S:
                movement.observed_s += t_s - self._last_frame_t_s
        self._last_frame_t_s = t_s

        points: list[tuple[float, float]] = []
        for box in detections["boxes"]:
            points.append(bottom_center(box))
        for start, end in self.tracker.update(points, t_s):
            movement.add_segment(start, end)

    def host_stats(
        self, parser: protocol.Parser, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build (and log) one host_stats row covering the time since the
        previous call.

        parser is the protocol.Parser feeding this pipeline; extra adds
        viewer-side columns.
        """
        now = time.monotonic()
        then, bytes_then, frames_then = self._at_last_host_stats
        frames_now = self.stats["pairs"] + self.stats["det_only"]
        elapsed = max(now - then, 1e-6)
        self._at_last_host_stats = (now, self.stats["bytes"], frames_now)

        clock_offset_us: int | str = ""
        clock_uncertainty_us: int | str = ""
        clock = self.clock.best()
        if clock is not None:
            clock_offset_us = round(clock[0])
            clock_uncertainty_us = round(clock[1])

        row = {
            "rx_records": self.stats["records"],
            "rx_bytes_per_s": round(
                (self.stats["bytes"] - bytes_then) / elapsed
            ),
            "rx_pairs_per_s": round((frames_now - frames_then) / elapsed, 2),
            "seq_gaps": self.stats["seq_gaps"],
            "crc_errors": parser.crc_errors,
            "resyncs": parser.resyncs,
            "oversize": parser.oversize,
            "missing_pairs": self.stats["missing_pairs"],
            "clock_offset_us": clock_offset_us,
            "clock_uncertainty_us": clock_uncertainty_us,
        }
        if extra is not None:
            row.update(extra)
        if self.logger is not None:
            self.logger.host_stats(row)
            self.logger.flush()
        return row
