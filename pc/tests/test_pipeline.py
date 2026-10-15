import json

import protocol as P
from pipeline import Pipeline
from replay import make_synthetic, replay


def hello(mode):
    return P.Record(P.HELLO, 0, 0, 0, json.dumps({"device_id": "t", "config": {"mode": mode}}).encode())


def jpeg(fid, seq, t=1000):
    return P.Record(P.JPEG_FRAME, seq, fid, t, P.JPEG_HDR.pack(320, 240, 12, t) + b"\xff\xd8x\xff\xd9")


def det(fid, seq, x1=10, t=1000):
    payload = P.DET_HDR.pack(0, 1, 320, 240) + P.BOX.pack(x1, 20, x1 + 40, 200, 0.9, 0)
    return P.Record(P.DETECTIONS, seq, fid, t, payload)


def run(records, mode="full_stream"):
    p = Pipeline()
    p.feed(hello(mode))
    for r in records:
        p.feed(r)
    return p


def test_pairs_by_frame_id():
    p = run([jpeg(1, 1), det(1, 2, x1=11), jpeg(2, 3), det(2, 4, x1=22)])
    assert p.stats["pairs"] == 2 and p.stats["missing_pairs"] == 0
    assert p.latest["frame_id"] == 2 and p.latest["boxes"][0]["x1"] == 22


def test_out_of_order_halves_still_pair_correctly():
    # DETECTIONS before its JPEG, and frame 2's halves interleaved with frame 1's
    p = run([det(1, 1, x1=11), jpeg(2, 2), jpeg(1, 3), det(2, 4, x1=22)])
    assert p.stats["pairs"] == 2 and p.stats["missing_pairs"] == 0
    assert p.latest["frame_id"] == 2 and p.latest["boxes"][0]["x1"] == 22


def test_detections_never_drawn_on_wrong_frame():
    # JPEG 1 arrives, then only DETECTIONS 2: must not complete frame 1 with frame 2's boxes
    p = run([jpeg(1, 1), det(2, 2, x1=22)])
    assert p.latest is None and p.stats["pairs"] == 0
    p.feed(jpeg(2, 3))
    assert p.latest["frame_id"] == 2 and p.latest["boxes"][0]["x1"] == 22
    assert p.stats["missing_pairs"] == 1  # frame 1 never got its DETECTIONS


def test_missing_jpeg_and_missing_detections_counted():
    p = run([jpeg(1, 1), det(1, 2), det(2, 3), jpeg(3, 4), jpeg(4, 5), det(4, 6)])
    assert p.stats["pairs"] == 2  # frames 1 and 4
    assert p.stats["missing_pairs"] == 2  # frame 2 (no JPEG), frame 3 (no DETECTIONS)
    assert p.stats["frame_id_gaps"] == 0  # those frame_ids were seen, so not "device drops"


def test_metadata_mode_detections_complete_alone():
    p = run([det(1, 1), det(2, 2), det(5, 3)], mode="metadata_stream")
    assert p.stats["det_only"] == 3 and p.stats["missing_pairs"] == 0
    assert p.stats["frame_id_gaps"] == 2 and p.latest["jpeg"] is None


def test_seq_gap_and_reconnect():
    p = run([jpeg(1, 1), det(1, 2), jpeg(2, 4), det(2, 5)])
    assert p.stats["seq_gaps"] == 1
    p.new_connection()
    p.feed(hello("full_stream"))  # seq restarts at 0 on a new connection: not a gap
    assert p.stats["seq_gaps"] == 1


def test_synthetic_run_replays_without_hardware(tmp_path):
    make_synthetic(tmp_path / "syn")
    p = Pipeline()
    replay(tmp_path / "syn", p)
    assert p.hello["device_id"] == "synthetic"
    assert p.stats["pairs"] == 68 and p.stats["missing_pairs"] == 1 and p.stats["frame_id_gaps"] == 1
    assert p.stats["seq_gaps"] == 0
    assert p.grid["frames"] == 60 and sum(map(sum, p.grid["counts"])) > 0
    assert p.movement.counts.sum() > 0 and 9 < p.movement.observed_s < 10.5


def test_committed_sample_matches_generator(tmp_path):
    from pathlib import Path
    sample = Path(__file__).resolve().parents[2] / "samples" / "synthetic_run" / "stream.gmtp"
    make_synthetic(tmp_path / "syn")
    assert sample.read_bytes() == (tmp_path / "syn" / "stream.gmtp").read_bytes()
