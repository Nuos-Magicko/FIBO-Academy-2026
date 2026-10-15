"""GMTP wire protocol: record framing, CRC check, resync, payload decoders.

Record: magic "GMTP" | ver u8 | type u8 | flags u16 | seq u32 | frame_id u32 |
        t_dev_us i64 | len u32 | payload[len] | crc32 u32 (zlib, over header+payload).
All little-endian. Run this file directly for the self-check.
"""
import json
import struct
import zlib
from typing import NamedTuple

MAGIC = b"GMTP"
HDR = struct.Struct("<4sBBHIIqI")  # 28 bytes
MAX_PAYLOAD = 64 * 1024
TYPES = {1: "HELLO", 2: "JPEG_FRAME", 3: "DETECTIONS", 4: "PRESENCE_GRID",
         5: "METRICS", 6: "EVENT", 7: "COMMAND", 8: "PONG"}
JPEG_HDR = struct.Struct("<HHB3xq")  # w, h, quality, t_capture_us
BOX = struct.Struct("<hhhhfB3x")     # x1, y1, x2, y2, score, cls


class Record(NamedTuple):
    type: int
    seq: int
    frame_id: int
    t_dev_us: int
    payload: bytes

    @property
    def name(self):
        return TYPES.get(self.type, f"T{self.type}")


def encode(type_, seq, frame_id, t_dev_us, payload, version=1, flags=0):
    body = HDR.pack(MAGIC, version, type_, flags, seq, frame_id, t_dev_us, len(payload)) + payload
    return body + struct.pack("<I", zlib.crc32(body))


class Parser:
    """Feed arbitrary byte chunks; get whole, CRC-checked records back."""

    def __init__(self):
        self.buf = bytearray()
        self.crc_errors = 0
        self.oversize = 0
        self.resyncs = 0  # times bytes were skipped to find the next magic

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(MAGIC)
            if i < 0:
                if len(self.buf) > 3:  # keep a possible partial magic
                    self.resyncs += 1
                    del self.buf[:-3]
                return out
            if i > 0:
                self.resyncs += 1
                del self.buf[:i]
            if len(self.buf) < HDR.size:
                return out
            _, _, typ, _, seq, fid, t, ln = HDR.unpack_from(self.buf)
            if ln > MAX_PAYLOAD:
                self.oversize += 1
                del self.buf[:1]
                continue
            total = HDR.size + ln + 4
            if len(self.buf) < total:
                return out
            (crc,) = struct.unpack_from("<I", self.buf, HDR.size + ln)
            if zlib.crc32(self.buf[:HDR.size + ln]) != crc:
                self.crc_errors += 1
                del self.buf[:1]
                continue
            out.append(Record(typ, seq, fid, t, bytes(self.buf[HDR.size:HDR.size + ln])))
            del self.buf[:total]


def decode_jpeg_frame(p):
    w, h, q, t_cap = JPEG_HDR.unpack_from(p)
    return {"w": w, "h": h, "quality": q, "t_capture_us": t_cap, "jpeg": p[JPEG_HDR.size:]}


def decode_detections(p):
    space, count = p[0], p[1]
    boxes = [dict(zip(("x1", "y1", "x2", "y2", "score", "cls"), BOX.unpack_from(p, 4 + i * BOX.size)))
             for i in range(count)]
    return {"space": "qvga_px" if space == 0 else space, "boxes": boxes}


def decode_metrics(p):
    return json.loads(p.decode())


if __name__ == "__main__":
    det = bytes([0, 1, 0, 0]) + BOX.pack(10, 20, 110, 220, 0.9, 0)
    jf = JPEG_HDR.pack(320, 240, 12, 123) + b"\xff\xd8jpeg\xff\xd9"
    stream = encode(2, 0, 7, 1, jf) + encode(3, 1, 7, 2, det) + encode(5, 2, 0, 3, b'{"temp_c":50.0}')

    # 1-byte fragmentation
    p = Parser()
    recs = [r for b in stream for r in p.feed(bytes([b]))]
    assert [r.name for r in recs] == ["JPEG_FRAME", "DETECTIONS", "METRICS"], recs
    assert decode_detections(recs[1].payload)["boxes"][0]["x2"] == 110
    assert decode_jpeg_frame(recs[0].payload)["w"] == 320
    assert decode_metrics(recs[2].payload)["temp_c"] == 50.0

    # garbage prefix + corrupted record in the middle + oversize length
    bad = bytearray(encode(3, 1, 7, 2, det))
    bad[-6] ^= 0xFF
    oversize = HDR.pack(MAGIC, 1, 2, 0, 9, 9, 9, MAX_PAYLOAD + 1)
    p = Parser()
    recs = p.feed(b"junkGMT" + encode(2, 0, 7, 1, jf) + bytes(bad) + oversize + encode(5, 2, 0, 3, b"{}"))
    assert [r.name for r in recs] == ["JPEG_FRAME", "METRICS"], recs
    assert p.crc_errors == 1 and p.oversize == 1, (p.crc_errors, p.oversize)
    print("protocol self-check OK")
