"""Run folder contents and schemas, the no-images-unless-recording rule, and
replay of a logged run."""

import csv
import json
from pathlib import Path
from typing import Any

from connection import protocol
from recording.logger import HOST_STATS_COLUMNS, METRICS_COLUMNS, RunLogger
from analysis.pipeline import Pipeline
from recording.replay import read_records, replay
from recording.synthetic import make_synthetic

JPEG_BYTES = b"\xff\xd8IMAGE\xff\xd9"
GRID_CELLS = 20 * 15
GRID_FIXED_COLUMNS = 8


def sample_records() -> list[protocol.Record]:
    hello = {"device_id": "dev1", "config": {"mode": "full_stream"}}
    metrics = {
        "mode": "full_stream",
        "fps_processed": 7.5,
        "stages": {"t_infer": {"n": 7, "mean": 110000.0}},
        "counters": {"tx_dropped": 3},
        "mem": {"int_min": 70000},
        "temp_c": 60.5,
        "unknown_new_key": 1,
    }
    jpeg_payload = (
        protocol.JPEG_FRAME_HEADER.pack(320, 240, 12, 2) + JPEG_BYTES
    )
    detections_payload = protocol.DETECTIONS_HEADER.pack(
        0, 1, 320, 240
    ) + protocol.WIRE_BOX.pack(1, 2, 3, 4, 0.5, 0)
    grid_payload = protocol.GRID_HEADER.pack(20, 15, 1, 9, 320, 240) + bytes(
        GRID_CELLS * 4
    )
    return [
        protocol.Record(protocol.HELLO, 0, 0, 1, json.dumps(hello).encode()),
        protocol.Record(protocol.JPEG_FRAME, 1, 1, 2, jpeg_payload),
        protocol.Record(protocol.DETECTIONS, 2, 1, 3, detections_payload),
        protocol.Record(
            protocol.METRICS, 3, 0, 4, json.dumps(metrics).encode()
        ),
        protocol.Record(
            protocol.EVENT,
            4,
            0,
            5,
            b'{"severity":"warn","code":"x","text":"y","t_event_us":5}',
        ),
        protocol.Record(protocol.PRESENCE_GRID, 5, 0, 6, grid_payload),
    ]


def log_sample(root: Path, recording: bool = False) -> RunLogger:
    logger = RunLogger(root=root, recording=recording)
    for record in sample_records():
        logger.record(record)
    return logger


def run_dir(logger: RunLogger) -> Path:
    """The logger's run folder; fails the test if it was never created."""
    assert logger.dir is not None
    return logger.dir


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path) as csv_file:
        return list(csv.DictReader(csv_file))


def test_run_folder_is_named_after_the_device(tmp_path: Path) -> None:
    logger = log_sample(tmp_path)
    logger.close()
    assert run_dir(logger).name.endswith("_dev1")
    assert (
        json.loads((run_dir(logger) / "hello.json").read_text())[0][
            "device_id"
        ]
        == "dev1"
    )


def test_detections_and_events_are_json_lines(tmp_path: Path) -> None:
    logger = log_sample(tmp_path)
    logger.close()
    detections = [
        json.loads(line)
        for line in (run_dir(logger) / "detections.jsonl")
        .read_text()
        .splitlines()
    ]
    assert detections[0]["frame_id"] == 1
    assert detections[0]["w"] == 320
    assert detections[0]["boxes"][0]["x2"] == 3
    event = json.loads((run_dir(logger) / "events.jsonl").read_text())
    assert event["code"] == "x"
    assert event["t_dev_us"] == 5


def test_metrics_csv_keeps_fixed_columns(tmp_path: Path) -> None:
    """Unknown keys are ignored; a stage without samples in the window stays as
    a blank column."""
    logger = log_sample(tmp_path)
    logger.close()
    rows = read_csv(run_dir(logger) / "metrics.csv")
    assert list(rows[0].keys()) == METRICS_COLUMNS
    assert rows[0]["stages.t_infer.mean"] == "110000.0"
    assert rows[0]["counters.tx_dropped"] == "3"
    assert rows[0]["stages.t_pre.mean"] == ""


def test_grid_and_host_stats_csv(tmp_path: Path) -> None:
    logger = log_sample(tmp_path)
    logger.host_stats({"rx_records": 6, "seq_gaps": 0})
    logger.close()
    with open(run_dir(logger) / "grid_snapshots.csv") as grid_file:
        grid_rows = list(csv.reader(grid_file))
    assert len(grid_rows[0]) == GRID_FIXED_COLUMNS + GRID_CELLS
    assert len(grid_rows[1]) == GRID_FIXED_COLUMNS + GRID_CELLS
    host_stats = read_csv(run_dir(logger) / "host_stats.csv")
    assert list(host_stats[0].keys()) == HOST_STATS_COLUMNS
    assert host_stats[0]["rx_records"] == "6"


def logged_jpeg(
    logger: RunLogger,
) -> tuple[dict[str, Any], list[protocol.Record]]:
    stream = list(read_records(run_dir(logger)))
    for record in stream:
        if record.type == protocol.JPEG_FRAME:
            frame = protocol.decode_jpeg_frame(record.payload)
            return frame, stream
    raise AssertionError("no JPEG_FRAME in the logged stream")


def test_without_recording_no_image_reaches_disk(tmp_path: Path) -> None:
    """The JPEG_FRAME header is kept so seq and frame_id stay continuous for
    replay."""
    logger = log_sample(tmp_path, recording=False)
    logger.close()
    frame, stream = logged_jpeg(logger)
    assert list((run_dir(logger) / "frames").iterdir()) == []
    assert frame["jpeg"] == b""
    assert frame["w"] == 320
    assert [record.seq for record in stream] == list(range(6))


def test_with_recording_images_are_saved(tmp_path: Path) -> None:
    logger = log_sample(tmp_path, recording=True)
    logger.close()
    frame, stream = logged_jpeg(logger)
    assert len(list((run_dir(logger) / "frames").iterdir())) == 1
    assert frame["jpeg"] == JPEG_BYTES
    assert [record.seq for record in stream] == list(range(6))


def test_logged_run_replays_with_the_same_counters(tmp_path: Path) -> None:
    """Only the byte count differs, because the logged stream has its JPEGs
    stripped."""
    make_synthetic(tmp_path / "syn")
    logger = RunLogger(root=tmp_path / "runs")
    live = Pipeline(logger=logger)
    replay(tmp_path / "syn", live)
    logger.close()
    replayed = Pipeline()
    replay(run_dir(logger), replayed)
    expected = dict(live.stats)
    expected["bytes"] = replayed.stats["bytes"]
    assert replayed.stats == expected
    assert replayed.movement is not None
    assert live.movement is not None
    assert (replayed.movement.counts == live.movement.counts).all()
