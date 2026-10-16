"""Run folder contents and schemas, the no-images-unless-recording rule, and replay of a logged run."""
import csv
import json

from connection import protocol as P
from recording.logger import HOST_STATS_COLUMNS, METRICS_COLUMNS, RunLogger
from analysis.pipeline import Pipeline
from recording.replay import read_records, replay
from recording.synthetic import make_synthetic

JPEG_BYTES = b"\xff\xd8IMAGE\xff\xd9"
GRID_CELLS = 20 * 15
GRID_FIXED_COLUMNS = 8


def sample_records():
    hello = {"device_id": "dev1", "config": {"mode": "full_stream"}}
    metrics = {"mode": "full_stream", "fps_processed": 7.5, "stages": {"t_infer": {"n": 7, "mean": 110000.0}},
               "counters": {"tx_dropped": 3}, "mem": {"int_min": 70000}, "temp_c": 60.5, "unknown_new_key": 1}
    return [
        P.Record(P.HELLO, 0, 0, 1, json.dumps(hello).encode()),
        P.Record(P.JPEG_FRAME, 1, 1, 2, P.JPEG_FRAME_HEADER.pack(320, 240, 12, 2) + JPEG_BYTES),
        P.Record(P.DETECTIONS, 2, 1, 3,
                 P.DETECTIONS_HEADER.pack(0, 1, 320, 240) + P.WIRE_BOX.pack(1, 2, 3, 4, 0.5, 0)),
        P.Record(P.METRICS, 3, 0, 4, json.dumps(metrics).encode()),
        P.Record(P.EVENT, 4, 0, 5, b'{"severity":"warn","code":"x","text":"y","t_event_us":5}'),
        P.Record(P.PRESENCE_GRID, 5, 0, 6, P.GRID_HEADER.pack(20, 15, 1, 9, 320, 240) + bytes(GRID_CELLS * 4)),
    ]


def log_sample(root, recording=False):
    logger = RunLogger(root=root, recording=recording)
    for record in sample_records():
        logger.record(record)
    return logger


def read_csv(path):
    with open(path) as csv_file:
        return list(csv.DictReader(csv_file))


def test_run_folder_is_named_after_the_device(tmp_path):
    logger = log_sample(tmp_path)
    logger.close()
    assert logger.dir.name.endswith("_dev1")
    assert json.loads((logger.dir / "hello.json").read_text())[0]["device_id"] == "dev1"


def test_detections_and_events_are_json_lines(tmp_path):
    logger = log_sample(tmp_path)
    logger.close()
    detections = [json.loads(line) for line in (logger.dir / "detections.jsonl").read_text().splitlines()]
    assert detections[0]["frame_id"] == 1 and detections[0]["w"] == 320 and detections[0]["boxes"][0]["x2"] == 3
    event = json.loads((logger.dir / "events.jsonl").read_text())
    assert event["code"] == "x" and event["t_dev_us"] == 5


def test_metrics_csv_keeps_fixed_columns(tmp_path):
    """Unknown keys are ignored; a stage without samples in the window stays as a blank column."""
    logger = log_sample(tmp_path)
    logger.close()
    rows = read_csv(logger.dir / "metrics.csv")
    assert list(rows[0].keys()) == METRICS_COLUMNS
    assert rows[0]["stages.t_infer.mean"] == "110000.0" and rows[0]["counters.tx_dropped"] == "3"
    assert rows[0]["stages.t_pre.mean"] == ""


def test_grid_and_host_stats_csv(tmp_path):
    logger = log_sample(tmp_path)
    logger.host_stats({"rx_records": 6, "seq_gaps": 0})
    logger.close()
    with open(logger.dir / "grid_snapshots.csv") as grid_file:
        grid_rows = list(csv.reader(grid_file))
    assert len(grid_rows[0]) == GRID_FIXED_COLUMNS + GRID_CELLS and len(grid_rows[1]) == GRID_FIXED_COLUMNS + GRID_CELLS
    host_stats = read_csv(logger.dir / "host_stats.csv")
    assert list(host_stats[0].keys()) == HOST_STATS_COLUMNS and host_stats[0]["rx_records"] == "6"


def logged_jpeg(logger):
    stream = list(read_records(logger.dir))
    frame = P.decode_jpeg_frame(next(r for r in stream if r.type == P.JPEG_FRAME).payload)
    return frame, stream


def test_without_recording_no_image_reaches_disk(tmp_path):
    """The JPEG_FRAME header is kept so seq and frame_id stay continuous for replay."""
    logger = log_sample(tmp_path, recording=False)
    logger.close()
    frame, stream = logged_jpeg(logger)
    assert list((logger.dir / "frames").iterdir()) == []
    assert frame["jpeg"] == b"" and frame["w"] == 320
    assert [record.seq for record in stream] == list(range(6))


def test_with_recording_images_are_saved(tmp_path):
    logger = log_sample(tmp_path, recording=True)
    logger.close()
    frame, stream = logged_jpeg(logger)
    assert len(list((logger.dir / "frames").iterdir())) == 1 and frame["jpeg"] == JPEG_BYTES
    assert [record.seq for record in stream] == list(range(6))


def test_logged_run_replays_with_the_same_counters(tmp_path):
    """Only the byte count differs, because the logged stream has its JPEGs stripped."""
    make_synthetic(tmp_path / "syn")
    logger = RunLogger(root=tmp_path / "runs")
    live = Pipeline(logger=logger)
    replay(tmp_path / "syn", live)
    logger.close()
    replayed = Pipeline()
    replay(logger.dir, replayed)
    assert replayed.stats == {**live.stats, "bytes": replayed.stats["bytes"]}
    assert (replayed.movement.counts == live.movement.counts).all()
