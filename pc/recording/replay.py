"""Play a saved session again through the same Pipeline the live viewer uses.

  python pc/main.py replay samples/synthetic_run                summary only, as fast as possible
  python pc/main.py replay runs/<run> --speed 1 --log           real-time pacing, write a new run log
  python pc/main.py viewer --replay samples/synthetic_run       the same data in the viewer window

A session is a run folder containing stream.gmtp (written by recording/logger.py), or a .gmtp file.
"""
import argparse
import json
import time
from pathlib import Path

from analysis.pipeline import Pipeline
from connection import protocol as P

READ_CHUNK_BYTES = 65536
MAX_PACING_SLEEP_S = 2.0


def stream_path(path):
    """A run folder's stream.gmtp, or the path itself if it is a file."""
    path = Path(path)
    return path / "stream.gmtp" if path.is_dir() else path


def read_records(path):
    parser = P.Parser()
    with open(stream_path(path), "rb") as stream:
        while chunk := stream.read(READ_CHUNK_BYTES):
            yield from parser.feed(chunk)


def paced(records, speed):
    """Sleep between records according to their device timestamps (speed 0 = no pacing)."""
    first_dev_us = started = None
    for record in records:
        if speed > 0:
            if first_dev_us is None:
                first_dev_us, started = record.t_dev_us, time.monotonic()
            wait = (record.t_dev_us - first_dev_us) / 1e6 / speed - (time.monotonic() - started)
            if wait > 0:
                time.sleep(min(wait, MAX_PACING_SLEEP_S))
        yield record


def replay(path, pipeline, speed=0.0):
    for record in paced(read_records(path), speed):
        pipeline.feed(record, P.record_size(record))


def summarize(pipeline, logger):
    movement = pipeline.movement
    return {"stats": pipeline.stats, "device": (pipeline.hello or {}).get("device_id"),
            "presence_frames": (pipeline.grid or {}).get("frames"),
            "movement_crossings": int(movement.counts.sum()) if movement else 0,
            "movement_observed_s": round(movement.observed_s, 2) if movement else 0,
            "log_dir": str(logger.dir) if logger else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="run directory or .gmtp file")
    parser.add_argument("--speed", type=float, default=0, help="1 = real time, 0 = as fast as possible")
    parser.add_argument("--log", action="store_true", help="also write a new run log under runs/")
    args = parser.parse_args()
    logger = None
    if args.log:
        from recording.logger import RunLogger
        logger = RunLogger()
    pipeline = Pipeline(logger=logger)
    replay(args.path, pipeline, args.speed)
    if logger:
        logger.close()
    print(json.dumps(summarize(pipeline, logger), indent=1))
    return 0
