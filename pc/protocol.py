"""GMTP wire protocol v1: record framing, CRC check, resync, payload codecs (docs/protocol.md).

Record: magic "GMTP" | ver u8 | type u8 | flags u16 | seq u32 | frame_id u32 |
        t_dev_us i64 | len u32 | payload[len] | crc32 u32 (zlib, over header+payload).
All little-endian. JSON payloads are parsed with json.loads only (no pickle/eval).
Run this file directly for the self-check.
"""
import json
import struct
import zlib
from typing import NamedTuple

MAGIC = b"GMTP"
HDR = struct.Struct("<4sBBHIIqI")  # 28 bytes
MAX_PAYLOAD = 64 * 1024
HELLO, JPEG_FRAME, DETECTIONS, PRESENCE_GRID, METRICS, EVENT, COMMAND, PONG, FRAME_TIMING = range(1, 10)
TYPES = {HELLO: "HELLO", JPEG_FRAME: "JPEG_FRAME", DETECTIONS: "DETECTIONS", PRESENCE_GRID: "PRESENCE_GRID",
         METRICS: "METRICS", EVENT: "EVENT", COMMAND: "COMMAND", PONG: "PONG", FRAME_TIMING: "FRAME_TIMING"}
JSON_TYPES = {HELLO, METRICS, EVENT, COMMAND, PONG, FRAME_TIMING}
JPEG_HDR = struct.Struct("<HHB3xq")    # w, h, quality, t_capture_us
DET_HDR = struct.Struct("<BBHH2x")     # space, count, w, h
BOX = struct.Struct("<hhhhfB3x")       # x1, y1, x2, y2, score, cls
GRID_HDR = struct.Struct("<HHIqHH")    # cols, rows, frames, elapsed_us, frame_w, frame_h


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


def encode_command(obj, seq=0):
    return encode(COMMAND, seq, 0, 0, json.dumps(obj).encode())


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
    space, count, w, h = DET_HDR.unpack_from(p)
    keys = ("x1", "y1", "x2", "y2", "score", "cls")
    boxes = [dict(zip(keys, BOX.unpack_from(p, DET_HDR.size + i * BOX.size))) for i in range(count)]
    return {"space": "frame_px" if space == 0 else space, "w": w, "h": h, "boxes": boxes}


def decode_grid(p):
    cols, rows, frames, elapsed, fw, fh = GRID_HDR.unpack_from(p)
    counts = list(struct.unpack_from(f"<{cols * rows}I", p, GRID_HDR.size))
    return {"cols": cols, "rows": rows, "frames": frames, "elapsed_us": elapsed, "frame_w": fw, "frame_h": fh,
            "counts": [counts[r * cols:(r + 1) * cols] for r in range(rows)]}


def decode_json(p):
    return json.loads(p.decode("utf-8"))


decode_metrics = decode_json


def flatten(d, prefix=""):
    """{"mem": {"int_min": 1}} -> {"mem.int_min": 1}, for CSV logging."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


if __name__ == "__main__":
    det = DET_HDR.pack(0, 1, 320, 240) + BOX.pack(10, 20, 110, 220, 0.9, 0)
    jf = JPEG_HDR.pack(320, 240, 12, 123) + b"\xff\xd8jpeg\xff\xd9"
    grid = GRID_HDR.pack(2, 1, 5, 1000, 320, 240) + struct.pack("<2I", 3, 4)
    stream = (encode(JPEG_FRAME, 0, 7, 1, jf) + encode(DETECTIONS, 1, 7, 2, det) +
              encode(METRICS, 2, 0, 3, b'{"temp_c":50.0,"mem":{"int_min":9}}') + encode(PRESENCE_GRID, 3, 0, 4, grid))

    # 1-byte fragmentation
    p = Parser()
    recs = [r for b in stream for r in p.feed(bytes([b]))]
    assert [r.name for r in recs] == ["JPEG_FRAME", "DETECTIONS", "METRICS", "PRESENCE_GRID"], recs
    d = decode_detections(recs[1].payload)
    assert d["boxes"][0]["x2"] == 110 and d["w"] == 320, d
    assert decode_jpeg_frame(recs[0].payload)["w"] == 320
    assert flatten(decode_json(recs[2].payload)) == {"temp_c": 50.0, "mem.int_min": 9}
    g = decode_grid(recs[3].payload)
    assert g["counts"] == [[3, 4]] and g["frames"] == 5, g

    # garbage prefix + corrupted record in the middle + oversize length
    bad = bytearray(encode(DETECTIONS, 1, 7, 2, det))
    bad[-6] ^= 0xFF
    oversize = HDR.pack(MAGIC, 1, 2, 0, 9, 9, 9, MAX_PAYLOAD + 1)
    p = Parser()
    recs = p.feed(b"junkGMT" + encode(JPEG_FRAME, 0, 7, 1, jf) + bytes(bad) + oversize + encode(METRICS, 2, 0, 3, b"{}"))
    assert [r.name for r in recs] == ["JPEG_FRAME", "METRICS"], recs
    assert p.crc_errors == 1 and p.oversize == 1, (p.crc_errors, p.oversize)
    assert Parser().feed(encode_command({"cmd": "ping"}))[0].name == "COMMAND"
    print("protocol self-check OK")
