import json
import struct
import zlib

import protocol as P

DET = P.DET_HDR.pack(0, 1, 320, 240) + P.BOX.pack(10, 20, 110, 220, 0.9, 0)
JPG = P.JPEG_HDR.pack(320, 240, 12, 123) + b"\xff\xd8jpeg\xff\xd9"


def stream():
    return (P.encode(P.JPEG_FRAME, 0, 7, 1, JPG) + P.encode(P.DETECTIONS, 1, 7, 2, DET)
            + P.encode(P.METRICS, 2, 0, 3, b'{"temp_c": 50}'))


def names(recs):
    return [r.name for r in recs]


def test_header_layout_matches_firmware():
    rec = P.encode(P.DETECTIONS, 5, 9, 1234, DET)
    assert rec[:4] == b"GMTP" and len(rec) == 28 + len(DET) + 4
    magic, ver, typ, flags, seq, fid, t, ln = P.HDR.unpack_from(rec)
    assert (ver, typ, seq, fid, t, ln) == (1, P.DETECTIONS, 5, 9, 1234, len(DET))
    assert struct.unpack_from("<I", rec, len(rec) - 4)[0] == zlib.crc32(rec[:-4])


def test_fragmentation_any_chunk_size():
    data = stream()
    for size in (1, 2, 3, 7, 28, 29, 64, len(data)):
        p = P.Parser()
        recs = [r for i in range(0, len(data), size) for r in p.feed(data[i:i + size])]
        assert names(recs) == ["JPEG_FRAME", "DETECTIONS", "METRICS"], size
        assert p.crc_errors == 0


def test_corruption_resyncs_and_counts():
    bad = bytearray(P.encode(P.DETECTIONS, 1, 7, 2, DET))
    bad[40] ^= 0x55
    p = P.Parser()
    recs = p.feed(b"\x00garbageGMT" + bytes(bad) + stream())
    assert names(recs) == ["JPEG_FRAME", "DETECTIONS", "METRICS"]
    assert p.crc_errors == 1 and p.resyncs >= 1


def test_oversize_length_rejected():
    p = P.Parser()
    hdr = P.HDR.pack(P.MAGIC, 1, P.JPEG_FRAME, 0, 0, 1, 0, P.MAX_PAYLOAD + 1)
    recs = p.feed(hdr + stream())
    assert p.oversize == 1
    assert names(recs) == ["JPEG_FRAME", "DETECTIONS", "METRICS"]


def test_truncated_stream_yields_nothing_partial():
    data = stream()
    p = P.Parser()
    assert names(p.feed(data[:-1])) == ["JPEG_FRAME", "DETECTIONS"]  # last record incomplete: held back


def test_payload_decoders():
    d = P.decode_detections(DET)
    assert d == {"space": "frame_px", "w": 320, "h": 240,
                 "boxes": [{"x1": 10, "y1": 20, "x2": 110, "y2": 220, "score": d["boxes"][0]["score"], "cls": 0}]}
    assert abs(d["boxes"][0]["score"] - 0.9) < 1e-6
    j = P.decode_jpeg_frame(JPG)
    assert (j["w"], j["h"], j["quality"], j["t_capture_us"], j["jpeg"]) == (320, 240, 12, 123, b"\xff\xd8jpeg\xff\xd9")
    g = P.decode_grid(P.GRID_HDR.pack(2, 2, 10, 5, 320, 240) + struct.pack("<4I", 1, 2, 3, 4))
    assert g["counts"] == [[1, 2], [3, 4]] and g["frames"] == 10
    cmd = P.Parser().feed(P.encode_command({"cmd": "ping", "t_host_us": 5}))[0]
    assert cmd.type == P.COMMAND and json.loads(cmd.payload) == {"cmd": "ping", "t_host_us": 5}


def test_flatten():
    assert P.flatten({"a": 1, "b": {"c": 2, "d": {"e": 3}}}) == {"a": 1, "b.c": 2, "b.d.e": 3}
