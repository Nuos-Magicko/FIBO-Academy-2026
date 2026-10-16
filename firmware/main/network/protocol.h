/**
 * @file protocol.h
 * GMTP wire protocol v1 (docs/protocol.md). All fields little-endian.
 *
 * Record: magic "GMTP" | version u8 | type u8 | flags u16 | seq u32 | frame_id u32 |
 *         t_dev_us i64 | len u32 | payload[len] | crc32 u32 (zlib CRC-32 over header + payload)
 */
#pragma once
#include <cstddef>
#include <cstdint>

constexpr uint8_t PROTO_VERSION = 1;
constexpr size_t PROTO_HEADER_BYTES = 28;
constexpr uint32_t PROTO_MAX_PAYLOAD = 65536;
/** Limit for COMMAND records arriving from the PC. */
constexpr uint32_t PROTO_MAX_INCOMING_PAYLOAD = 1024;

enum record_type_t : uint8_t {
    /** JSON: device facts and full configuration. */
    REC_HELLO = 1,
    /** Binary: width u16, height u16, quality u8, pad[3], t_capture_us i64, then the JPEG. */
    REC_JPEG_FRAME = 2,
    /** Binary: space u8, count u8, width u16, height u16, pad u16, then count boxes of 16 bytes. */
    REC_DETECTIONS = 3,
    /** Binary: see grid_payload(). */
    REC_PRESENCE_GRID = 4,
    /** JSON: one metrics window. */
    REC_METRICS = 5,
    /** JSON: severity, code, text. */
    REC_EVENT = 6,
    /** JSON, PC to device. */
    REC_COMMAND = 7,
    /** JSON: reply to a ping command. */
    REC_PONG = 8,
    /** JSON: per-frame stage times, only when metrics_verbose is on. */
    REC_FRAME_TIMING = 9,
};

/**
 * Sends one record whose payload is the concatenation of two parts (the second may be empty).
 * Increments *seq. Returns false on a socket error.
 */
bool proto_send(int sock, uint32_t *seq, uint8_t type, uint32_t frame_id, const void *part1, uint32_t part1_len,
                const void *part2 = nullptr, uint32_t part2_len = 0);

/** Reassembles incoming records with magic resync, a length limit and a CRC check. */
struct proto_receiver_t {
    uint8_t buffer[PROTO_HEADER_BYTES + PROTO_MAX_INCOMING_PAYLOAD + 4];
    size_t length;
};

using proto_record_handler_t = void (*)(uint8_t type, const uint8_t *payload, uint32_t len, void *context);

/** Appends received bytes and calls on_record for every complete record whose CRC is valid. */
void proto_receiver_feed(proto_receiver_t *receiver, const uint8_t *data, size_t length,
                         proto_record_handler_t on_record, void *context);
