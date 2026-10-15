import csv
import json

import protocol as P
from logger import HOST_STATS_COLUMNS, METRICS_COLUMNS, RunLogger
from pipeline import Pipeline
from replay import make_synthetic, read_records, replay


def records():
    hello = {"device_id": "dev1", "config": {"mode": "full_stream"}}
    metrics = {"mode": "full_stream", "fps_processed": 7.5, "stages": {"t_infer": {"n": 7, "mean": 110000.0}},
               "counters": {"tx_dropped": 3}, "mem": {"int_min": 70000}, "temp_c": 60.5, "unknown_new_key": 1}
    return [
        P.Record(P.HELLO, 0, 0, 1, json.dumps(hello).encode()),
        P.Record(P.JPEG_FRAME, 1, 1, 2, P.JPEG_HDR.pack(320, 240, 12, 2) + b"\xff\xd8IMAGE\xff\xd9"),
        P.Record(P.DETECTIONS, 2, 1, 3, P.DET_HDR.pack(0, 1, 320, 240) + P.BOX.pack(1, 2, 3, 4, 0.5, 0)),
        P.Record(P.METRICS, 3, 0, 4, json.dumps(metrics).encode()),
        P.Record(P.EVENT, 4, 0, 5, b'{"severity":"warn","code":"x","text":"y","t_event_us":5}'),
        P.Record(P.PRESENCE_GRID, 5, 0, 6, P.GRID_HDR.pack(20, 15, 1, 9, 320, 240) + bytes(300 * 4)),
    ]


def test_run_files_and_schema(tmp_path):
    lg = RunLogger(root=tmp_path)
    for r in records():
        lg.record(r)
    lg.host_stats({"rx_records": 6, "seq_gaps": 0})
    lg.close()
    d = lg.dir
    assert d.name.endswith("_dev1")
    assert json.loads((d / "hello.json").read_text())[0]["device_id"] == "dev1"

    det = [json.loads(line) for line in (d / "detections.jsonl").read_text().splitlines()]
    assert det[0]["frame_id"] == 1 and det[0]["w"] == 320 and det[0]["boxes"][0]["x2"] == 3

    rows = list(csv.DictReader(open(d / "metrics.csv")))
    assert list(rows[0].keys()) == METRICS_COLUMNS  # fixed columns; unknown keys ignored
    assert rows[0]["stages.t_infer.mean"] == "110000.0" and rows[0]["counters.tx_dropped"] == "3"
    assert rows[0]["stages.t_pre.mean"] == ""  # stage absent in this window: blank, column still there

    ev = json.loads((d / "events.jsonl").read_text())
    assert ev["code"] == "x" and ev["t_dev_us"] == 5
    grid = list(csv.reader(open(d / "grid_snapshots.csv")))
    assert len(grid[0]) == 8 + 300 and len(grid[1]) == 8 + 300
    hs = list(csv.DictReader(open(d / "host_stats.csv")))
    assert list(hs[0].keys()) == HOST_STATS_COLUMNS and hs[0]["rx_records"] == "6"


def test_jpegs_only_when_recording(tmp_path):
    for recording in (False, True):
        lg = RunLogger(root=tmp_path / str(recording), recording=recording)
        for r in records():
            lg.record(r)
        lg.close()
        frames = list((lg.dir / "frames").iterdir())
        stream = list(read_records(lg.dir))
        jf = P.decode_jpeg_frame(next(r for r in stream if r.type == P.JPEG_FRAME).payload)
        if recording:
            assert len(frames) == 1 and jf["jpeg"] == b"\xff\xd8IMAGE\xff\xd9"
        else:
            assert frames == [] and jf["jpeg"] == b"" and jf["w"] == 320  # header kept, image stripped
        assert [r.seq for r in stream] == list(range(6))  # stream stays continuous


def test_logged_run_replays_identically(tmp_path):
    make_synthetic(tmp_path / "syn")
    lg = RunLogger(root=tmp_path / "runs")
    first = Pipeline(logger=lg)
    replay(tmp_path / "syn", first)
    lg.close()
    second = Pipeline()
    replay(lg.dir, second)
    assert second.stats == {**first.stats, "bytes": second.stats["bytes"]}  # bytes differ: JPEGs stripped
    assert (second.movement.counts == first.movement.counts).all()
