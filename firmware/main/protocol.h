// GMTP wire protocol v1 (docs/protocol.md). Little-endian.
// magic "GMTP" | ver u8 | type u8 | flags u16 | seq u32 | frame_id u32 | t_dev_us i64 | len u32 | payload | crc32 u32
#pragma once
#include <cstddef>
#include <cstdint>

#define PROTO_VERSION 1
#define PROTO_HDR_LEN 28
#define PROTO_MAX_PAYLOAD 65536
#define PROTO_RX_MAX_PAYLOAD 1024 // COMMAND records from the PC

enum rec_type_t : uint8_t {
    REC_HELLO = 1,         // JSON
    REC_JPEG_FRAME = 2,    // binary: w u16, h u16, quality u8, pad[3], t_capture_us i64, JPEG
    REC_DETECTIONS = 3,    // binary: space u8, count u8, w u16, h u16, pad u16, count x box(16 B)
    REC_PRESENCE_GRID = 4, // binary: see grid.cpp
    REC_METRICS = 5,       // JSON
    REC_EVENT = 6,         // JSON
    REC_COMMAND = 7,       // JSON, PC -> device
    REC_PONG = 8,          // JSON
    REC_FRAME_TIMING = 9,  // JSON, per frame, only when metrics_verbose
};

// Sends one record (payload may be split in two parts). Increments *seq. false = socket error.
bool proto_send(int sock, uint32_t *seq, uint8_t type, uint32_t frame_id, const void *a, uint32_t alen,
                const void *b = nullptr, uint32_t blen = 0);

// Incoming stream reassembly with magic resync, length limit and CRC check.
struct proto_rx_t {
    uint8_t buf[PROTO_HDR_LEN + PROTO_RX_MAX_PAYLOAD + 4];
    size_t len;
};
// Appends data; for each complete valid record calls cb(type, payload, len, ctx).
void proto_rx_feed(proto_rx_t *rx, const uint8_t *data, size_t n,
                   void (*cb)(uint8_t type, const uint8_t *payload, uint32_t len, void *ctx), void *ctx);
