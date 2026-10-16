"""Play a saved session again through the same Pipeline the live viewer uses.

Summary only, as fast as possible:
  python pc/main.py replay samples/synthetic_run
Real-time pacing, and write a new run log:
  python pc/main.py replay runs/<run> --speed 1 --log
The same data in the viewer window:
  python pc/main.py viewer --replay samples/synthetic_run

A session is a run folder containing stream.gmtp (written by
recording/logger.py), or a .gmtp file.
"""

import argparse
import json
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from analysis.pipeline import Pipeline
from connection import protocol
from recording.logger import RunLogger

READ_CHUNK_BYTES = 65536
MAX_PACING_SLEEP_S = 2.0


def stream_path(path: Path | str) -> Path:
    """A run folder's stream.gmtp, or the path itself if it is a file."""
    path = Path(path)
    if path.is_dir():
        return path / "stream.gmtp"
    return path


def read_records(path: Path | str) -> Iterator[protocol.Record]:
    parser = protocol.Parser()
    with open(stream_path(path), "rb") as stream:
        while True:
            chunk = stream.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            for record in parser.feed(chunk):
                yield record


def paced(
    records: Iterable[protocol.Record], speed: float
) -> Iterator[protocol.Record]:
    """Sleep between records according to their device timestamps (speed 0 = no
    pacing)."""
    first_dev_us: int | None = None
    started = 0.0
    for record in records:
        if speed > 0:
            if first_dev_us is None:
                first_dev_us = record.t_dev_us
                started = time.monotonic()
            due_s = (record.t_dev_us - first_dev_us) / 1e6 / speed
            elapsed_s = time.monotonic() - started
            wait = due_s - elapsed_s
            if wait > 0:
                time.sleep(min(wait, MAX_PACING_SLEEP_S))
        yield record


def replay(path: Path | str, pipeline: Pipeline, speed: float = 0.0) -> None:
    for record in paced(read_records(path), speed):
        pipeline.feed(record, protocol.record_size(record))


def summarize(pipeline: Pipeline, logger: RunLogger | None) -> dict[str, Any]:
    device = None
    if pipeline.hello is not None:
        device = pipeline.hello.get("device_id")
    presence_frames = None
    if pipeline.grid is not None:
        presence_frames = pipeline.grid.get("frames")
    movement_crossings = 0
    movement_observed_s = 0.0
    if pipeline.movement is not None:
        movement_crossings = int(pipeline.movement.counts.sum())
        movement_observed_s = round(pipeline.movement.observed_s, 2)
    log_dir = None
    if logger is not None:
        log_dir = str(logger.dir)
    return {
        "stats": pipeline.stats,
        "device": device,
        "presence_frames": presence_frames,
        "movement_crossings": movement_crossings,
        "movement_observed_s": movement_observed_s,
        "log_dir": log_dir,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="run directory or .gmtp file")
    parser.add_argument(
        "--speed",
        type=float,
        default=0,
        help="1 = real time, 0 = as fast as possible",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="also write a new run log under runs/",
    )
    args = parser.parse_args()
    logger = None
    if args.log:
        logger = RunLogger()
    pipeline = Pipeline(logger=logger)
    replay(args.path, pipeline, args.speed)
    if logger is not None:
        logger.close()
    print(json.dumps(summarize(pipeline, logger), indent=1))
    return 0
