"""Record framing and payload codecs: what the firmware sends must parse, whatever the chunking."""
import json
import struct
import zlib

from connection import protocol as P

DETECTIONS_PAYLOAD = P.DETECTIONS_HEADER.pack(0, 1, 320, 240) + P.WIRE_BOX.pack(10, 20, 110, 220, 0.9, 0)
JPEG_BYTES = b"\xff\xd8jpeg\xff\xd9"
JPEG_FRAME_PAYLOAD = P.JPEG_FRAME_HEADER.pack(320, 240, 12, 123) + JPEG_BYTES
EXPECTED_NAMES = ["JPEG_FRAME", "DETECTIONS", "METRICS"]


def three_record_stream():
    return (P.encode(P.JPEG_FRAME, 0, 7, 1, JPEG_FRAME_PAYLOAD) + P.encode(P.DETECTIONS, 1, 7, 2, DETECTIONS_PAYLOAD)
            + P.encode(P.METRICS, 2, 0, 3, b'{"temp_c": 50}'))


def names(records):
    return [record.name for record in records]


def test_header_layout_matches_firmware():
    record = P.encode(P.DETECTIONS, 5, 9, 1234, DETECTIONS_PAYLOAD)
    assert record[:4] == b"GMTP" and len(record) == 28 + len(DETECTIONS_PAYLOAD) + 4
    _, version, record_type, _, seq, frame_id, t_dev_us, length = P.HEADER.unpack_from(record)
    assert (version, record_type, seq, frame_id, t_dev_us, length) == (1, P.DETECTIONS, 5, 9, 1234,
                                                                       len(DETECTIONS_PAYLOAD))
    assert struct.unpack_from("<I", record, len(record) - 4)[0] == zlib.crc32(record[:-4])


def test_record_size_counts_header_payload_and_crc():
    record = P.Parser().feed(P.encode(P.DETECTIONS, 0, 1, 0, DETECTIONS_PAYLOAD))[0]
    assert P.record_size(record) == 28 + len(DETECTIONS_PAYLOAD) + 4


def test_fragmentation_at_any_chunk_size():
    data = three_record_stream()
    for chunk_size in (1, 2, 3, 7, 28, 29, 64, len(data)):
        parser = P.Parser()
        records = [r for start in range(0, len(data), chunk_size) for r in parser.feed(data[start:start + chunk_size])]
        assert names(records) == EXPECTED_NAMES, chunk_size
        assert parser.crc_errors == 0


def test_corrupted_record_is_dropped_and_the_parser_resyncs():
    corrupted = bytearray(P.encode(P.DETECTIONS, 1, 7, 2, DETECTIONS_PAYLOAD))
    corrupted[40] ^= 0x55
    parser = P.Parser()
    records = parser.feed(b"\x00garbageGMT" + bytes(corrupted) + three_record_stream())
    assert names(records) == EXPECTED_NAMES
    assert parser.crc_errors == 1 and parser.resyncs >= 1


def test_oversize_length_is_rejected():
    parser = P.Parser()
    oversize_header = P.HEADER.pack(P.MAGIC, 1, P.JPEG_FRAME, 0, 0, 1, 0, P.MAX_PAYLOAD + 1)
    records = parser.feed(oversize_header + three_record_stream())
    assert parser.oversize == 1
    assert names(records) == EXPECTED_NAMES


def test_incomplete_last_record_is_held_back():
    assert names(P.Parser().feed(three_record_stream()[:-1])) == ["JPEG_FRAME", "DETECTIONS"]


def test_payload_decoders():
    detections = P.decode_detections(DETECTIONS_PAYLOAD)
    score = detections["boxes"][0]["score"]
    assert detections == {"space": "frame_px", "w": 320, "h": 240,
                          "boxes": [{"x1": 10, "y1": 20, "x2": 110, "y2": 220, "score": score, "cls": 0}]}
    assert abs(score - 0.9) < 1e-6
    frame = P.decode_jpeg_frame(JPEG_FRAME_PAYLOAD)
    assert (frame["w"], frame["h"], frame["quality"], frame["t_capture_us"], frame["jpeg"]) == (320, 240, 12, 123,
                                                                                               JPEG_BYTES)
    grid = P.decode_grid(P.GRID_HEADER.pack(2, 2, 10, 5, 320, 240) + struct.pack("<4I", 1, 2, 3, 4))
    assert grid["counts"] == [[1, 2], [3, 4]] and grid["frames"] == 10
    command = P.Parser().feed(P.encode_command({"cmd": "ping", "t_host_us": 5}))[0]
    assert command.type == P.COMMAND and json.loads(command.payload) == {"cmd": "ping", "t_host_us": 5}


def test_flatten():
    assert P.flatten({"a": 1, "b": {"c": 2, "d": {"e": 3}}}) == {"a": 1, "b.c": 2, "b.d.e": 3}
