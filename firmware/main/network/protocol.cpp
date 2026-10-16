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

static constexpr char MAGIC[4] = {'G', 'M', 'T', 'P'};
static constexpr size_t MAGIC_BYTES = sizeof MAGIC;
static constexpr size_t LENGTH_FIELD_OFFSET = 24;
static constexpr size_t TYPE_FIELD_OFFSET = 5;
static constexpr size_t CRC_BYTES = 4;

static bool send_all(int sock, const void *data, size_t length)
{
    const uint8_t *cursor = (const uint8_t *)data;
    while (length) {
        int sent = send(sock, cursor, length, 0);
        if (sent <= 0) return false;
        cursor += sent;
        length -= sent;
    }
    return true;
}

static void write_header(uint8_t *header, uint8_t type, uint32_t seq, uint32_t frame_id, uint32_t payload_len)
{
    const uint16_t flags = 0;
    const int64_t t_dev_us = esp_timer_get_time();
    memcpy(header, MAGIC, MAGIC_BYTES);
    header[4] = PROTO_VERSION;
    header[TYPE_FIELD_OFFSET] = type;
    memcpy(header + 6, &flags, 2);
    memcpy(header + 8, &seq, 4);
    memcpy(header + 12, &frame_id, 4);
    memcpy(header + 16, &t_dev_us, 8);
    memcpy(header + LENGTH_FIELD_OFFSET, &payload_len, 4);
}

bool proto_send(int sock, uint32_t *seq, uint8_t type, uint32_t frame_id, const void *part1, uint32_t part1_len,
                const void *part2, uint32_t part2_len)
{
    uint8_t header[PROTO_HEADER_BYTES];
    const uint32_t payload_len = part1_len + part2_len;
    write_header(header, type, *seq, frame_id, payload_len);
    uint32_t crc = esp_rom_crc32_le(0, header, sizeof header);
    if (part1_len) crc = esp_rom_crc32_le(crc, (const uint8_t *)part1, part1_len);
    if (part2_len) crc = esp_rom_crc32_le(crc, (const uint8_t *)part2, part2_len);
    (*seq)++;
    bool sent = send_all(sock, header, sizeof header) && (!part1_len || send_all(sock, part1, part1_len)) &&
                (!part2_len || send_all(sock, part2, part2_len)) && send_all(sock, &crc, CRC_BYTES);
    if (sent) g_counters.bytes_sent += sizeof header + payload_len + CRC_BYTES;
    return sent;
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
    while (offset + MAGIC_BYTES <= receiver->length && memcmp(receiver->buffer + offset, MAGIC, MAGIC_BYTES) != 0)
        offset++;
    bool magic_found = offset + MAGIC_BYTES <= receiver->length;
    if (!magic_found) offset = receiver->length > MAGIC_BYTES - 1 ? receiver->length - (MAGIC_BYTES - 1) : 0;
    if (offset) discard_front(receiver, offset);
}

/** Extracts every complete record at the front of the buffer. */
static void extract_records(proto_receiver_t *receiver, proto_record_handler_t on_record, void *context)
{
    for (;;) {
        skip_to_magic(receiver);
        if (receiver->length < PROTO_HEADER_BYTES) return;
        uint32_t payload_len;
        memcpy(&payload_len, receiver->buffer + LENGTH_FIELD_OFFSET, 4);
        if (payload_len > PROTO_MAX_INCOMING_PAYLOAD) {
            g_counters.rx_oversize++;
            discard_front(receiver, 1);
            continue;
        }
        size_t record_len = PROTO_HEADER_BYTES + payload_len + CRC_BYTES;
        if (receiver->length < record_len) return;
        uint32_t received_crc, computed_crc = esp_rom_crc32_le(0, receiver->buffer, PROTO_HEADER_BYTES + payload_len);
        memcpy(&received_crc, receiver->buffer + PROTO_HEADER_BYTES + payload_len, CRC_BYTES);
        if (received_crc != computed_crc) {
            g_counters.rx_crc_errors++;
            discard_front(receiver, 1);
            continue;
        }
        on_record(receiver->buffer[TYPE_FIELD_OFFSET], receiver->buffer + PROTO_HEADER_BYTES, payload_len, context);
        discard_front(receiver, record_len);
    }
}

void proto_receiver_feed(proto_receiver_t *receiver, const uint8_t *data, size_t length,
                         proto_record_handler_t on_record, void *context)
{
    while (length) {
        size_t chunk = sizeof receiver->buffer - receiver->length;
        if (chunk > length) chunk = length;
        memcpy(receiver->buffer + receiver->length, data, chunk);
        receiver->length += chunk;
        data += chunk;
        length -= chunk;
        extract_records(receiver, on_record, context);
    }
}
