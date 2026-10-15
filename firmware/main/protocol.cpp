#include "protocol.h"
#include <cstring>
#include "app.h"
#include "esp_rom_crc.h"
#include "esp_timer.h"
#include "lwip/sockets.h"

static bool send_all(int s, const void *p, size_t n)
{
    const uint8_t *c = (const uint8_t *)p;
    while (n) {
        int r = send(s, c, n, 0);
        if (r <= 0) return false;
        c += r;
        n -= r;
    }
    return true;
}

bool proto_send(int sock, uint32_t *seq, uint8_t type, uint32_t frame_id, const void *a, uint32_t alen,
                const void *b, uint32_t blen)
{
    uint8_t h[PROTO_HDR_LEN];
    uint16_t flags = 0;
    int64_t t = esp_timer_get_time();
    uint32_t len = alen + blen;
    memcpy(h, "GMTP", 4);
    h[4] = PROTO_VERSION;
    h[5] = type;
    memcpy(h + 6, &flags, 2);
    memcpy(h + 8, seq, 4);
    memcpy(h + 12, &frame_id, 4);
    memcpy(h + 16, &t, 8);
    memcpy(h + 24, &len, 4);
    // esp_rom_crc32_le(0, ...) chained == zlib.crc32 (verified against the PC parser on hardware)
    uint32_t crc = esp_rom_crc32_le(0, h, sizeof h);
    if (alen) crc = esp_rom_crc32_le(crc, (const uint8_t *)a, alen);
    if (blen) crc = esp_rom_crc32_le(crc, (const uint8_t *)b, blen);
    (*seq)++;
    bool ok = send_all(sock, h, sizeof h) && (!alen || send_all(sock, a, alen)) && (!blen || send_all(sock, b, blen)) &&
              send_all(sock, &crc, 4);
    if (ok) g_cnt.bytes_sent += sizeof h + len + 4;
    return ok;
}

void proto_rx_feed(proto_rx_t *rx, const uint8_t *data, size_t n,
                   void (*cb)(uint8_t, const uint8_t *, uint32_t, void *), void *ctx)
{
    while (n) {
        size_t take = sizeof rx->buf - rx->len;
        if (take > n) take = n;
        memcpy(rx->buf + rx->len, data, take);
        rx->len += take;
        data += take;
        n -= take;

        for (;;) {
            // resync on magic
            size_t i = 0;
            while (i + 4 <= rx->len && memcmp(rx->buf + i, "GMTP", 4) != 0) i++;
            if (i + 4 > rx->len) i = rx->len > 3 ? rx->len - 3 : 0; // keep a possible partial magic
            if (i) {
                memmove(rx->buf, rx->buf + i, rx->len - i);
                rx->len -= i;
            }
            if (rx->len < PROTO_HDR_LEN) break;
            uint32_t len;
            memcpy(&len, rx->buf + 24, 4);
            if (len > PROTO_RX_MAX_PAYLOAD) {
                g_cnt.rx_oversize++;
                memmove(rx->buf, rx->buf + 1, --rx->len);
                continue;
            }
            size_t total = PROTO_HDR_LEN + len + 4;
            if (rx->len < total) break;
            uint32_t crc, calc = esp_rom_crc32_le(0, rx->buf, PROTO_HDR_LEN + len);
            memcpy(&crc, rx->buf + PROTO_HDR_LEN + len, 4);
            if (crc != calc) {
                g_cnt.rx_crc_errors++;
                memmove(rx->buf, rx->buf + 1, --rx->len);
                continue;
            }
            cb(rx->buf[5], rx->buf + PROTO_HDR_LEN, len, ctx);
            memmove(rx->buf, rx->buf + total, rx->len - total);
            rx->len -= total;
        }
    }
}
