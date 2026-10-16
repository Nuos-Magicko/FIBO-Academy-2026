"""Reading and writing the messages ("records") the board and the laptop send
each other.

The data arrives over the network as one long stream of bytes. This file cuts
it back into records. Every record has:
  - a header: the letters "GMTP" (so the start of a record can be found), the
    record type (picture, boxes, measurements, ...), a sequence number, the
    frame_id and the board's time;
  - the payload: the actual data;
  - a checksum (CRC) to detect damaged data.

If bytes are damaged, the record is skipped and counted (crc_errors), and the
reader searches for the next "GMTP". Exact byte layout:

Record: magic "GMTP" | version u8 | type u8 | flags u16 | seq u32 |
        frame_id u32 | t_dev_us i64 | len u32 | payload[len] | crc32 u32
        (zlib CRC-32 over header + payload).
All fields are little-endian (lowest byte first). JSON payloads are read with
json.loads only.
Full description: docs/protocol.md.
"""

import json
import struct
import zlib
from typing import Any
from typing import NamedTuple

MAGIC = b"GMTP"
MAX_PAYLOAD = 64 * 1024
CRC = struct.Struct("<I")

HEADER = struct.Struct("<4sBBHIIqI")
"""magic, version, type, flags, seq, frame_id, t_dev_us, payload length (28
bytes)."""
JPEG_FRAME_HEADER = struct.Struct("<HHB3xq")
"""width, height, JPEG quality, t_capture_us; the JPEG bytes follow."""
DETECTIONS_HEADER = struct.Struct("<BBHH2x")
"""coordinate space (0 = frame pixels), box count, width, height; the boxes
follow."""
WIRE_BOX = struct.Struct("<hhhhfB3x")
"""x1, y1, x2, y2, score, class id (0 = person)."""
GRID_HEADER = struct.Struct("<HHIqHH")
"""cols, rows, processed frames, elapsed_us, frame width, frame height; u32
counts follow, row-major."""

HELLO = 1
JPEG_FRAME = 2
DETECTIONS = 3
PRESENCE_GRID = 4
METRICS = 5
EVENT = 6
COMMAND = 7
PONG = 8
FRAME_TIMING = 9
MODES = [
    "full_stream",
    "metadata_stream",
    "detect_no_stream",
    "camera_only",
    "inference_only",
]
"""The board's experiment modes, as named in HELLO.config.mode and "set"."""
TYPE_NAMES = {
    HELLO: "HELLO",
    JPEG_FRAME: "JPEG_FRAME",
    DETECTIONS: "DETECTIONS",
    PRESENCE_GRID: "PRESENCE_GRID",
    METRICS: "METRICS",
    EVENT: "EVENT",
    COMMAND: "COMMAND",
    PONG: "PONG",
    FRAME_TIMING: "FRAME_TIMING",
}


class Record(NamedTuple):
    """One CRC-checked record."""

    type: int
    seq: int
    frame_id: int
    t_dev_us: int
    payload: bytes

    @property
    def name(self) -> str:
        return TYPE_NAMES.get(self.type, f"T{self.type}")


def encode(
    record_type: int,
    seq: int,
    frame_id: int,
    t_dev_us: int,
    payload: bytes,
    version: int = 1,
    flags: int = 0,
) -> bytes:
    """Frame a payload as a complete record, CRC included."""
    body = (
        HEADER.pack(
            MAGIC,
            version,
            record_type,
            flags,
            seq,
            frame_id,
            t_dev_us,
            len(payload),
        )
        + payload
    )
    return body + CRC.pack(zlib.crc32(body))


def record_size(record: Record) -> int:
    """Bytes the record occupies on the wire."""
    return HEADER.size + len(record.payload) + CRC.size


def encode_command(command: dict[str, Any], seq: int = 0) -> bytes:
    """Frame a COMMAND record carrying `command` as JSON."""
    return encode(COMMAND, seq, 0, 0, json.dumps(command).encode())


class Parser:
    """Reassembles records from arbitrary byte chunks.

    Bytes before a magic are skipped (counted in `resyncs`); a length above
    MAX_PAYLOAD (`oversize`) or a failed CRC (`crc_errors`) skips one byte and
    searches for the next magic.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.crc_errors = 0
        self.oversize = 0
        self.resyncs = 0

    def feed(self, data: bytes) -> list[Record]:
        """Append bytes and return every record completed by them."""
        self.buffer += data
        records = []
        while True:
            if not self._align_to_magic():
                break
            if len(self.buffer) < HEADER.size:
                break
            (
                magic,
                version,
                record_type,
                flags,
                seq,
                frame_id,
                t_dev_us,
                length,
            ) = HEADER.unpack_from(self.buffer)
            if length > MAX_PAYLOAD:
                self.oversize += 1
                del self.buffer[:1]
                continue
            record_end = HEADER.size + length + CRC.size
            if len(self.buffer) < record_end:
                break
            crc = CRC.unpack_from(self.buffer, HEADER.size + length)[0]
            if zlib.crc32(self.buffer[: HEADER.size + length]) != crc:
                self.crc_errors += 1
                del self.buffer[:1]
                continue
            payload_start = HEADER.size
            payload_end = HEADER.size + length
            payload = bytes(self.buffer[payload_start:payload_end])
            records.append(
                Record(record_type, seq, frame_id, t_dev_us, payload)
            )
            del self.buffer[:record_end]
        return records

    def _align_to_magic(self) -> bool:
        """Drop bytes before the next magic. Returns False if no magic is
        buffered yet, keeping a trailing partial magic that the next chunk may
        complete.
        """
        start = self.buffer.find(MAGIC)
        if start < 0:
            partial_magic_bytes = len(MAGIC) - 1
            if len(self.buffer) > partial_magic_bytes:
                self.resyncs += 1
                del self.buffer[:-partial_magic_bytes]
            return False
        if start > 0:
            self.resyncs += 1
            del self.buffer[:start]
        return True


def decode_jpeg_frame(payload: bytes) -> dict[str, Any]:
    """JPEG_FRAME payload -> {w, h, quality, t_capture_us, jpeg}."""
    width, height, quality, t_capture_us = JPEG_FRAME_HEADER.unpack_from(
        payload
    )
    image_start = JPEG_FRAME_HEADER.size
    return {
        "w": width,
        "h": height,
        "quality": quality,
        "t_capture_us": t_capture_us,
        "jpeg": payload[image_start:],
    }


def decode_detections(payload: bytes) -> dict[str, Any]:
    """DETECTIONS payload -> {space, w, h, boxes: [{x1, y1, x2, y2, score,
    cls}]} in frame pixels."""
    space, count, width, height = DETECTIONS_HEADER.unpack_from(payload)
    boxes = []
    for index in range(count):
        offset = DETECTIONS_HEADER.size + index * WIRE_BOX.size
        x1, y1, x2, y2, score, cls = WIRE_BOX.unpack_from(payload, offset)
        boxes.append(
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "score": score,
                "cls": cls,
            }
        )
    if space == 0:
        space = "frame_px"
    return {"space": space, "w": width, "h": height, "boxes": boxes}


def decode_grid(payload: bytes) -> dict[str, Any]:
    """PRESENCE_GRID payload -> {cols, rows, frames, elapsed_us, frame_w,
    frame_h, counts[rows][cols]}."""
    cols, rows, frames, elapsed_us, frame_width, frame_height = (
        GRID_HEADER.unpack_from(payload)
    )
    counts = struct.unpack_from(f"<{cols * rows}I", payload, GRID_HEADER.size)
    counts_by_row = []
    for row in range(rows):
        first = row * cols
        last = first + cols
        counts_by_row.append(list(counts[first:last]))
    return {
        "cols": cols,
        "rows": rows,
        "frames": frames,
        "elapsed_us": elapsed_us,
        "frame_w": frame_width,
        "frame_h": frame_height,
        "counts": counts_by_row,
    }


def decode_json(payload: bytes) -> dict[str, Any]:
    """Payload of HELLO, METRICS, EVENT, COMMAND, PONG or FRAME_TIMING."""
    return json.loads(payload.decode("utf-8"))


def flatten(nested: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """{"mem": {"int_min": 1}} -> {"mem.int_min": 1}, for CSV columns."""
    flat = {}
    for key, value in nested.items():
        if isinstance(value, dict):
            flat.update(flatten(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat
