"""Frame join by frame_id, loss counters, and replay of the synthetic run."""
import json
from pathlib import Path

from connection import protocol as P
from analysis.pipeline import Pipeline
from recording.replay import replay
from recording.synthetic import make_synthetic

COMMITTED_SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "synthetic_run" / "stream.gmtp"


def hello(mode):
    return P.Record(P.HELLO, 0, 0, 0, json.dumps({"device_id": "t", "config": {"mode": mode}}).encode())


def jpeg(frame_id, seq, t_us=1000):
    return P.Record(P.JPEG_FRAME, seq, frame_id, t_us, P.JPEG_FRAME_HEADER.pack(320, 240, 12, t_us) + b"\xff\xd8x\xff\xd9")


def detections(frame_id, seq, x1=10, t_us=1000):
    payload = P.DETECTIONS_HEADER.pack(0, 1, 320, 240) + P.WIRE_BOX.pack(x1, 20, x1 + 40, 200, 0.9, 0)
    return P.Record(P.DETECTIONS, seq, frame_id, t_us, payload)


def run(records, mode="full_stream"):
    pipeline = Pipeline()
    pipeline.feed(hello(mode))
    for record in records:
        pipeline.feed(record)
    return pipeline


def test_pairs_by_frame_id():
    pipeline = run([jpeg(1, 1), detections(1, 2, x1=11), jpeg(2, 3), detections(2, 4, x1=22)])
    assert pipeline.stats["pairs"] == 2 and pipeline.stats["missing_pairs"] == 0
    assert pipeline.latest["frame_id"] == 2 and pipeline.latest["boxes"][0]["x1"] == 22


def test_out_of_order_halves_still_pair_correctly():
    """DETECTIONS before its JPEG, and frame 2's halves interleaved with frame 1's."""
    pipeline = run([detections(1, 1, x1=11), jpeg(2, 2), jpeg(1, 3), detections(2, 4, x1=22)])
    assert pipeline.stats["pairs"] == 2 and pipeline.stats["missing_pairs"] == 0
    assert pipeline.latest["frame_id"] == 2 and pipeline.latest["boxes"][0]["x1"] == 22


def test_detections_are_never_drawn_on_the_wrong_frame():
    """JPEG 1 then only DETECTIONS 2: frame 1 must not be completed with frame 2's boxes."""
    pipeline = run([jpeg(1, 1), detections(2, 2, x1=22)])
    assert pipeline.latest is None and pipeline.stats["pairs"] == 0
    pipeline.feed(jpeg(2, 3))
    assert pipeline.latest["frame_id"] == 2 and pipeline.latest["boxes"][0]["x1"] == 22
    assert pipeline.stats["missing_pairs"] == 1


def test_missing_halves_count_as_missing_pairs_not_frame_id_gaps():
    """Frame 2 has no JPEG and frame 3 no DETECTIONS; their ids were seen, so they are not device drops."""
    pipeline = run([jpeg(1, 1), detections(1, 2), detections(2, 3), jpeg(3, 4), jpeg(4, 5), detections(4, 6)])
    assert pipeline.stats["pairs"] == 2
    assert pipeline.stats["missing_pairs"] == 2
    assert pipeline.stats["frame_id_gaps"] == 0


def test_metadata_mode_detections_complete_alone():
    pipeline = run([detections(1, 1), detections(2, 2), detections(5, 3)], mode="metadata_stream")
    assert pipeline.stats["det_only"] == 3 and pipeline.stats["missing_pairs"] == 0
    assert pipeline.stats["frame_id_gaps"] == 2 and pipeline.latest["jpeg"] is None


def test_seq_restarting_on_a_new_connection_is_not_a_gap():
    pipeline = run([jpeg(1, 1), detections(1, 2), jpeg(2, 4), detections(2, 5)])
    assert pipeline.stats["seq_gaps"] == 1
    pipeline.new_connection()
    pipeline.feed(hello("full_stream"))
    assert pipeline.stats["seq_gaps"] == 1


def test_synthetic_run_replays_without_hardware(tmp_path):
    make_synthetic(tmp_path / "syn")
    pipeline = Pipeline()
    replay(tmp_path / "syn", pipeline)
    assert pipeline.hello["device_id"] == "synthetic"
    assert pipeline.stats["pairs"] == 68
    assert pipeline.stats["missing_pairs"] == 1 and pipeline.stats["frame_id_gaps"] == 1
    assert pipeline.stats["seq_gaps"] == 0
    assert pipeline.grid["frames"] == 60 and sum(map(sum, pipeline.grid["counts"])) > 0
    assert pipeline.movement.counts.sum() > 0 and 9 < pipeline.movement.observed_s < 10.5


def test_committed_sample_matches_the_generator(tmp_path):
    make_synthetic(tmp_path / "syn")
    assert COMMITTED_SAMPLE.read_bytes() == (tmp_path / "syn" / "stream.gmtp").read_bytes()
