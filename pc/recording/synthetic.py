"""The synthetic sample: a made-up session for trying the viewer.

No board is needed.

  python pc/main.py make-synthetic samples/synthetic_run

Three box-shaped "walkers" cross a 320 x 240 grey picture. The result is
always byte-for-byte the same (fixed random seed), so tests can compare
against the committed copy. Three problems are added on purpose so the loss
counters can be checked:
  - frame_id 40 is missing        (like a frame the board dropped)
  - frame 30 has no DETECTIONS    (a missing pair)
  - walker 0 is missed on frame 20 (a missed detection)
HELLO says device_id "synthetic", so it can never be mistaken for a real
recording.
"""

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from analysis.heatmap import COLS, ROWS, presence_add
from connection import protocol

SYNTHETIC_WIDTH = 320
SYNTHETIC_HEIGHT = 240
SYNTHETIC_JPEG_QUALITY = 60
SYNTHETIC_START_US = 1_000_000
WALKER_PATHS = [
    ((-30, 200), (350, 170)),
    ((330, 120), (-20, 150)),
    ((150, 245), (190, 90)),
]
"""Each walker's feet move in a straight line from start to end over the whole
run."""
MISSED_DETECTION = (0, 20)
"""(walker, frame index): that walker is not detected in that frame."""
DROPPED_FRAME_ID = 40
FRAME_ID_WITHOUT_DETECTIONS = 30
GRID_EVERY_N_FRAMES = 10


def _walker_box(
    path: tuple[tuple[int, int], tuple[int, int]], progress: float
) -> tuple[float, float, list[int]]:
    """Feet position along the path; the box grows toward the bottom (fake
    perspective)."""
    start, end = path
    x0, y0 = start
    x1, y1 = end
    feet_x = x0 + (x1 - x0) * progress
    feet_y = y0 + (y1 - y0) * progress
    box_height = int(40 + 70 * feet_y / SYNTHETIC_HEIGHT)
    box_width = box_height // 3
    box = [
        int(feet_x - box_width / 2),
        int(feet_y - box_height),
        int(feet_x + box_width / 2),
        int(feet_y),
    ]
    return feet_x, feet_y, box


def _detected_box(box: list[int], score: float) -> dict[str, Any]:
    return {
        "x1": max(box[0], 0),
        "y1": max(box[1], 0),
        "x2": min(box[2], SYNTHETIC_WIDTH - 1),
        "y2": min(box[3], SYNTHETIC_HEIGHT - 1),
        "score": score,
    }


def _render_frame(
    frame_index: int, frame_count: int, rng: np.random.Generator
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Draw one synthetic frame; return (image, detected boxes)."""
    image = np.full((SYNTHETIC_HEIGHT, SYNTHETIC_WIDTH, 3), 90, np.uint8)
    horizon = SYNTHETIC_HEIGHT // 2
    image[horizon:] = 120
    cv2.putText(
        image,
        "SYNTHETIC",
        (8, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        1,
    )
    boxes = []
    for walker, path in enumerate(WALKER_PATHS):
        feet_x, feet_y, box = _walker_box(
            path, frame_index / (frame_count - 1)
        )
        cv2.rectangle(
            image,
            (box[0], box[1]),
            (box[2], box[3]),
            (40, 40 + 60 * walker, 160),
            -1,
        )
        x_in_view = feet_x >= 0 and feet_x < SYNTHETIC_WIDTH
        y_in_view = feet_y - 5 >= 0 and feet_y - 5 < SYNTHETIC_HEIGHT
        missed = (walker, frame_index) == MISSED_DETECTION
        if x_in_view and y_in_view and not missed:
            score = float(0.75 + 0.2 * rng.random())
            boxes.append(_detected_box(box, score))
    return image, boxes


def _synthetic_hello() -> dict[str, Any]:
    return {
        "proto_version": 1,
        "device_id": "synthetic",
        "fw_version": "synthetic",
        "synthetic": True,
        "sensor_pid": "n/a",
        "grid_cols": COLS,
        "grid_rows": ROWS,
        "box_coords": "frame_px",
        "config": {
            "mode": "full_stream",
            "frame_size": "QVGA",
            "jpeg_quality": SYNTHETIC_JPEG_QUALITY,
            "metrics_interval_ms": 1000,
            "grid_every_n": GRID_EVERY_N_FRAMES,
        },
    }


def _detections_payload(boxes: list[dict[str, Any]]) -> bytes:
    payload = protocol.DETECTIONS_HEADER.pack(
        0, len(boxes), SYNTHETIC_WIDTH, SYNTHETIC_HEIGHT
    )
    for box in boxes:
        payload += protocol.WIRE_BOX.pack(
            box["x1"], box["y1"], box["x2"], box["y2"], box["score"], 0
        )
    return payload


def _append_record(
    records: list[bytes],
    record_type: int,
    t_dev_us: int,
    payload: bytes,
    frame_id: int = 0,
) -> None:
    """Encodes one record; its seq is its position in the list."""
    seq = len(records)
    records.append(
        protocol.encode(record_type, seq, frame_id, t_dev_us, payload)
    )


def _write_readme(
    out: Path, out_dir: Path | str, frame_count: int, fps: int
) -> None:
    (out / "README.md").write_text(
        "# Synthetic run (generated, not recorded)\n\n"
        f"Made by `python pc/main.py make-synthetic {out_dir}`."
        f" {frame_count} frames at {fps} fps, 320x240, three box-shaped\n"
        "walkers. On purpose: frame_id 40 is missing (a device drop),"
        " frame 30 has no DETECTIONS (a missing pair),\n"
        "walker 0 is not detected on frame 20 (a missed detection)."
        f" Replay: `python pc/main.py replay {out_dir}`\n"
        f"or `python pc/main.py viewer --replay {out_dir}`.\n"
    )


def make_synthetic(
    out_dir: Path | str, seconds: int = 10, fps: int = 7, seed: int = 1
) -> Path:
    """Write the synthetic run (stream.gmtp + README.md) into out_dir and
    return its path."""
    rng = np.random.default_rng(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records: list[bytes] = []
    _append_record(
        records, protocol.HELLO, 0, json.dumps(_synthetic_hello()).encode()
    )
    event = {
        "severity": "info",
        "code": "synthetic_run",
        "text": "generated by pc/main.py make-synthetic, not a recording",
        "t_event_us": 0,
    }
    _append_record(records, protocol.EVENT, 0, json.dumps(event).encode())
    presence = np.zeros((ROWS, COLS), dtype=np.int64)
    frame_count = seconds * fps
    frames_sent = 0
    for frame_index in range(frame_count):
        frame_id = frame_index + 1
        t_us = SYNTHETIC_START_US + frame_index * 1_000_000 // fps
        image, boxes = _render_frame(frame_index, frame_count, rng)
        if frame_id == DROPPED_FRAME_ID:
            continue
        encoded_ok, encoded = cv2.imencode(
            ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, SYNTHETIC_JPEG_QUALITY]
        )
        jpeg = encoded.tobytes()
        jpeg_header = protocol.JPEG_FRAME_HEADER.pack(
            SYNTHETIC_WIDTH, SYNTHETIC_HEIGHT, SYNTHETIC_JPEG_QUALITY, t_us
        )
        _append_record(
            records, protocol.JPEG_FRAME, t_us, jpeg_header + jpeg, frame_id
        )
        if frame_id == FRAME_ID_WITHOUT_DETECTIONS:
            continue
        _append_record(
            records,
            protocol.DETECTIONS,
            t_us,
            _detections_payload(boxes),
            frame_id,
        )
        presence_add(presence, boxes, SYNTHETIC_WIDTH, SYNTHETIC_HEIGHT)
        frames_sent += 1
        if frames_sent % GRID_EVERY_N_FRAMES == 0:
            grid_header = protocol.GRID_HEADER.pack(
                COLS,
                ROWS,
                frames_sent,
                t_us - SYNTHETIC_START_US,
                SYNTHETIC_WIDTH,
                SYNTHETIC_HEIGHT,
            )
            _append_record(
                records,
                protocol.PRESENCE_GRID,
                t_us,
                grid_header + presence.astype("<u4").tobytes(),
            )
        if frame_index % fps == 0:
            metrics = {
                "synthetic": True,
                "mode": "full_stream",
                "fps_processed": fps,
                "fps_sent": fps,
                "uptime_s": t_us / 1e6,
            }
            _append_record(
                records, protocol.METRICS, t_us, json.dumps(metrics).encode()
            )
    (out / "stream.gmtp").write_bytes(b"".join(records))
    _write_readme(out, out_dir, frame_count, fps)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write the synthetic sample session."
    )
    parser.add_argument(
        "out_dir", help="folder to write, e.g. samples/synthetic_run"
    )
    args = parser.parse_args()
    written = make_synthetic(args.out_dir)
    print("wrote", written)
    return 0
