/**
 * @file protocol.cpp
 * GMTP record sending and incoming-record reassembly.
 *
 * Chained esp_rom_crc32_le(0, ...) calls produce the same value as zlib.crc32 on the PC; this was
 * verified on hardware (0 CRC errors over a one-hour soak).
 */
#include <cstring>
#include "network/protocol.h"
#include "telemetry/metrics.h"
#include "esp_rom_crc.h"
#include "esp_timer.h"
#include "lwip/sockets.h"

static constexpr char MAGIC[PROTO_MAGIC_BYTES] = {'G', 'M', 'T', 'P'};
static constexpr uint32_t CRC_START = 0;
static constexpr int NO_FLAGS = 0;

static bool send_all(int sock, const void *data, size_t length)
{
    const uint8_t *cursor = static_cast<const uint8_t *>(data);
    while (length > 0) {
        int sent = send(sock, cursor, length, NO_FLAGS);
        if (sent <= 0) {
            return false;
        }
        cursor += sent;
        length -= sent;
    }
    return true;
}

static uint32_t crc_of(uint32_t crc, const void *data, size_t length)
{
    return esp_rom_crc32_le(crc, static_cast<const uint8_t *>(data), length);
}

static record_header_t make_header(record_type_t type, uint32_t seq, uint32_t frame_id, uint32_t payload_length)
{
    record_header_t header = {};
    memcpy(header.magic, MAGIC, PROTO_MAGIC_BYTES);
    header.version = PROTO_VERSION;
    header.type = type;
    header.flags = 0;
    header.seq = seq;
    header.frame_id = frame_id;
    header.t_dev_us = esp_timer_get_time();
    header.payload_length = payload_length;
    return header;
}

bool proto_send(int sock, uint32_t *seq, record_type_t type, uint32_t frame_id, const void *part1,
                uint32_t part1_len, const void *part2, uint32_t part2_len)
{
    const uint32_t payload_len = part1_len + part2_len;
    record_header_t header = make_header(type, *seq, frame_id, payload_len);
    uint32_t crc = crc_of(CRC_START, &header, sizeof(header));
    if (part1_len > 0) {
        crc = crc_of(crc, part1, part1_len);
    }
    if (part2_len > 0) {
        crc = crc_of(crc, part2, part2_len);
    }
    (*seq)++;

    if (!send_all(sock, &header, sizeof(header))) {
        return false;
    }
    if (part1_len > 0) {
        if (!send_all(sock, part1, part1_len)) {
            return false;
        }
    }
    if (part2_len > 0) {
        if (!send_all(sock, part2, part2_len)) {
            return false;
        }
    }
    if (!send_all(sock, &crc, PROTO_CRC_BYTES)) {
        return false;
    }
    g_counters.bytes_sent += sizeof(header) + payload_len + PROTO_CRC_BYTES;
    return true;
}

static void discard_front(proto_receiver_t *receiver, size_t count)
{
    memmove(receiver->buffer, receiver->buffer + count, receiver->length - count);
    receiver->length -= count;
}

/** Drops bytes before the next magic, keeping a trailing partial magic that may complete later. */
static void skip_to_magic(proto_receiver_t *receiver)
{
    size_t offset = 0;
    bool magic_found = false;
    while (offset + PROTO_MAGIC_BYTES <= receiver->length) {
        if (memcmp(receiver->buffer + offset, MAGIC, PROTO_MAGIC_BYTES) == 0) {
            magic_found = true;
            break;
        }
        offset++;
    }
    if (!magic_found) {
        size_t partial_magic_bytes = PROTO_MAGIC_BYTES - 1;
        if (receiver->length > partial_magic_bytes) {
            offset = receiver->length - partial_magic_bytes;
        } else {
            offset = 0;
        }
    }
    if (offset > 0) {
        discard_front(receiver, offset);
    }
}

/** Extracts every complete record at the front of the buffer. */
static void extract_records(proto_receiver_t *receiver, proto_record_handler_t on_record, void *context)
{
    while (true) {
        skip_to_magic(receiver);
        if (receiver->length < PROTO_HEADER_BYTES) {
            return;
        }
        record_header_t header;
        memcpy(&header, receiver->buffer, sizeof(header));
        if (header.payload_length > PROTO_MAX_INCOMING_PAYLOAD) {
            g_counters.rx_oversize++;
            discard_front(receiver, 1);
            continue;
        }
        size_t crc_offset = PROTO_HEADER_BYTES + header.payload_length;
        size_t record_len = crc_offset + PROTO_CRC_BYTES;
        if (receiver->length < record_len) {
            return;
        }
        uint32_t computed_crc = crc_of(CRC_START, receiver->buffer, crc_offset);
        uint32_t received_crc;
        memcpy(&received_crc, receiver->buffer + crc_offset, PROTO_CRC_BYTES);
        if (received_crc != computed_crc) {
            g_counters.rx_crc_errors++;
            discard_front(receiver, 1);
            continue;
        }
        on_record(header.type, receiver->buffer + PROTO_HEADER_BYTES, header.payload_length, context);
        discard_front(receiver, record_len);
    }
}

void proto_receiver_feed(proto_receiver_t *receiver, const uint8_t *data, size_t length,
                         proto_record_handler_t on_record, void *context)
{
    while (length > 0) {
        size_t chunk = sizeof(receiver->buffer) - receiver->length;
        if (chunk > length) {
            chunk = length;
        }
        memcpy(receiver->buffer + receiver->length, data, chunk);
        receiver->length += chunk;
        data += chunk;
        length -= chunk;
        extract_records(receiver, on_record, context);
    }
}
