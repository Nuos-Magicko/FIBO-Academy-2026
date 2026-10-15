// Vision task (core 1): capture -> [copy] -> decode -> detect -> grid -> enqueue, per mode.
#include <cstring>
#include "app.h"
#include "dl_image_jpeg.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "pedestrian_detect.hpp"

static const char *TAG = "vision";

extern const uint8_t pedestrian_jpg_start[] asm("_binary_pedestrian_jpg_start");
extern const uint8_t pedestrian_jpg_end[] asm("_binary_pedestrian_jpg_end");

QueueHandle_t g_free_q, g_ready_q;
static bundle_t s_pool[POOL_N];

void pool_init()
{
    g_free_q = xQueueCreate(POOL_N, sizeof(bundle_t *));
    g_ready_q = xQueueCreate(READY_DEPTH, sizeof(bundle_t *));
    for (auto &b : s_pool) {
        b.jpeg = (uint8_t *)heap_caps_malloc(JPEG_MAX, MALLOC_CAP_SPIRAM);
        bundle_t *p = &b;
        xQueueSend(g_free_q, &p, 0);
    }
}

// Same steps as dl::detect::DetectImpl::run (esp-dl 3.3.13), with a timestamp between each, so
// t_pre / t_infer / t_post are measured separately. Same model, thresholds and postprocessor.
class TimedPico : public pedestrian_detect::Pico {
public:
    TimedPico(float score_thr) : Pico("pedestrian_detect_pico_s8_v1.espdl", score_thr, NMS_THR) {}
    std::list<dl::detect::result_t> &run_timed(const dl::image::img_t &img, int32_t *t)
    {
        int64_t a = esp_timer_get_time();
        m_image_preprocessor->preprocess(img);
        int64_t b = esp_timer_get_time();
        m_model->run();
        int64_t c = esp_timer_get_time();
        m_postprocessor->clear_result();
        m_postprocessor->postprocess();
        auto &res = m_postprocessor->get_result(img.width, img.height);
        int64_t d = esp_timer_get_time();
        t[ST_PRE] = b - a;
        t[ST_INFER] = c - b;
        t[ST_POST] = d - c;
        return res;
    }
};

static int16_t clamp(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

// Free bundle, or steal the oldest queued one (drop-oldest). Metadata modes wait first.
static bundle_t *take_bundle(bool wait)
{
    bundle_t *b;
    if (xQueueReceive(g_free_q, &b, wait ? pdMS_TO_TICKS(METADATA_ENQUEUE_WAIT_MS) : 0) == pdTRUE) return b;
    if (xQueueReceive(g_ready_q, &b, pdMS_TO_TICKS(100)) == pdTRUE) {
        g_cnt.tx_dropped++;
        return b;
    }
    return nullptr;
}

static void enqueue(bundle_t *b, bool wait)
{
    if (wait && xQueueSend(g_ready_q, &b, pdMS_TO_TICKS(METADATA_ENQUEUE_WAIT_MS)) == pdTRUE) return;
    while (xQueueSend(g_ready_q, &b, 0) != pdTRUE) {
        bundle_t *old;
        if (xQueueReceive(g_ready_q, &old, 0) == pdTRUE) {
            xQueueSend(g_free_q, &old, 0);
            g_cnt.tx_dropped++;
        }
    }
}

static void record_stages(const int32_t *t)
{
    for (int i = 0; i < ST_COUNT; i++)
        if (t[i] >= 0) metrics_add((stage_t)i, t[i]);
}

static void fill_boxes(bundle_t *b, std::list<dl::detect::result_t> &res, int w, int h)
{
    b->nbox = 0;
    for (auto &r : res) {
        if (b->nbox == MAX_BOXES) break;
        b->box[b->nbox++] = {clamp(r.box[0], 0, w - 1), clamp(r.box[1], 0, h - 1), clamp(r.box[2], 0, w - 1),
                             clamp(r.box[3], 0, h - 1), r.score};
    }
}

static uint32_t s_frame_id;

// inference_only: embedded still, decoded and detected each run; DETECTIONS only (no JPEG).
static void still_once(TimedPico *det)
{
    int32_t t[ST_COUNT];
    for (auto &x : t) x = -1;
    dl::image::jpeg_img_t jpeg = {.data = (void *)pedestrian_jpg_start,
                                  .data_len = (size_t)(pedestrian_jpg_end - pedestrian_jpg_start)};
    int64_t t0 = esp_timer_get_time();
    auto img = dl::image::sw_decode_jpeg(jpeg, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
    t[ST_DECODE] = esp_timer_get_time() - t0;
    if (!img.data) {
        g_cnt.decode_fail++;
        return;
    }
    auto &res = det->run_timed(img, t);
    bundle_t *b = take_bundle(true);
    if (b) {
        b->frame_id = ++s_frame_id;
        b->t_capture_us = t0;
        b->w = img.width;
        b->h = img.height;
        b->jpeg_len = 0;
        fill_boxes(b, res, img.width, img.height);
        memcpy(b->t_us, t, sizeof t); // before hand-off; see live_once
        int64_t te = esp_timer_get_time();
        enqueue(b, true);
        t[ST_ENQUEUE] = esp_timer_get_time() - te;
    }
    t[ST_PROC] = esp_timer_get_time() - t0;
    heap_caps_free(img.data);
    g_cnt.still_runs++;
    g_cnt.frames_processed++;
    record_stages(t);
}

// Live modes. Returns after one camera frame.
static void live_once(TimedPico *det, const app_cfg_t &cfg, uint32_t &cap_index)
{
    int32_t t[ST_COUNT];
    for (auto &x : t) x = -1;
    int64_t t0 = esp_timer_get_time();
    camera_fb_t *fb = esp_camera_fb_get();
    int64_t t_got = esp_timer_get_time();
    t[ST_WAIT_FRAME] = t_got - t0;
    if (!fb) {
        uint32_t n = ++g_cnt.cap_timeout;
        if (n == 1 || n % 100 == 0) event_post(EV_WARN, "capture_timeout", "total=%u", (unsigned)n);
        return;
    }
    g_cnt.cap_ok++;
    if (cfg.mode == MODE_CAMERA_ONLY) {
        esp_camera_fb_return(fb);
        metrics_add(ST_WAIT_FRAME, t[ST_WAIT_FRAME]);
        return;
    }
    if (cap_index++ % cfg.infer_every_n) { // baseline streams only inferred frames
        esp_camera_fb_return(fb);
        g_cnt.cadence_skipped++;
        return;
    }

    const bool full = cfg.mode == MODE_FULL_STREAM, meta = cfg.mode == MODE_METADATA_STREAM;
    const uint16_t w = fb->width, h = fb->height;
    bundle_t *b = nullptr;
    const uint8_t *src = fb->buf;
    size_t src_len = fb->len;
    if (full || meta) {
        b = take_bundle(meta);
        if (!b) {
            esp_camera_fb_return(fb);
            return;
        }
    }
    if (full) {
        if (fb->len > JPEG_MAX) {
            g_cnt.oversize++;
            esp_camera_fb_return(fb);
            xQueueSend(g_free_q, &b, 0);
            return;
        }
        int64_t tc = esp_timer_get_time();
        memcpy(b->jpeg, fb->buf, fb->len);
        b->jpeg_len = fb->len;
        t[ST_COPY] = esp_timer_get_time() - tc;
        esp_camera_fb_return(fb); // camera buffer back right after the copy; decode from the copy
        fb = nullptr;
        src = b->jpeg;
        src_len = b->jpeg_len;
    } else if (b) {
        b->jpeg_len = 0;
    }

    int64_t td = esp_timer_get_time();
    dl::image::jpeg_img_t jpeg = {.data = (void *)src, .data_len = src_len};
    auto img = dl::image::sw_decode_jpeg(jpeg, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
    t[ST_DECODE] = esp_timer_get_time() - td;
    if (fb) esp_camera_fb_return(fb); // non-full modes: returned right after decode
    if (!img.data) {
        g_cnt.decode_fail++;
        if (b) xQueueSend(g_free_q, &b, 0);
        return;
    }

    auto &res = det->run_timed(img, t);
    heap_caps_free(img.data);

    static bundle_t s_scratch; // detect_no_stream has no bundle; boxes still feed the grid
    bundle_t *dst = b ? b : &s_scratch;
    fill_boxes(dst, res, w, h);
    int64_t tg = esp_timer_get_time();
    grid_add(dst->box, dst->nbox);
    t[ST_GRID] = esp_timer_get_time() - tg;

    uint32_t fid = ++s_frame_id;
    if (b) {
        b->frame_id = fid;
        b->t_capture_us = t_got;
        b->w = w;
        b->h = h;
        // Per-frame copy is taken before the hand-off, so it cannot include t_enqueue / t_proc
        // (both stay -1 in FRAME_TIMING; the METRICS window has them).
        memcpy(b->t_us, t, sizeof t);
        int64_t te = esp_timer_get_time();
        enqueue(b, meta);
        t[ST_ENQUEUE] = esp_timer_get_time() - te;
    }
    t[ST_PROC] = esp_timer_get_time() - t_got;
    g_cnt.frames_processed++;
    record_stages(t);
}

void vision_task(void *)
{
    app_cfg_t cfg = settings_get();
    uint32_t gen = settings_generation();
    TimedPico *det = new TimedPico(cfg.score_thr);
    { // self-test on the embedded still (expected output: docs/test_report.md)
        dl::image::jpeg_img_t jpeg = {.data = (void *)pedestrian_jpg_start,
                                      .data_len = (size_t)(pedestrian_jpg_end - pedestrian_jpg_start)};
        auto img = dl::image::sw_decode_jpeg(jpeg, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
        int32_t t[ST_COUNT];
        auto &res = det->run_timed(img, t);
        for (auto &r : res)
            ESP_LOGI(TAG, "SELFTEST box score=%.3f x1=%d y1=%d x2=%d y2=%d", r.score, r.box[0], r.box[1], r.box[2],
                     r.box[3]);
        event_post(EV_INFO, "self_test", "still %dx%d boxes=%u t_pre=%d t_infer=%d t_post=%d (first run)", img.width,
                   img.height, (unsigned)res.size(), (int)t[ST_PRE], (int)t[ST_INFER], (int)t[ST_POST]);
        heap_caps_free(img.data);
    }
    bool cam_on = cfg.mode != MODE_INFERENCE_ONLY && camera_start(cfg);
    grid_reset(320, 240);
    if (cam_on) {
        camera_fb_t *fb = esp_camera_fb_get(); // learn the real frame size for the grid
        if (fb) {
            grid_reset(fb->width, fb->height);
            esp_camera_fb_return(fb);
        }
    }
    uint32_t cap_index = 0;
    bool still_done_reported = false;

    for (;;) {
        vTaskDelay(1); // let IDLE1 run (task watchdog); 1 tick = 1 ms at CONFIG_FREERTOS_HZ=1000

        if (settings_generation() != gen) {
            app_cfg_t n = settings_get();
            gen = settings_generation();
            bool cam_params = n.frame_size != cfg.frame_size || n.jpeg_quality != cfg.jpeg_quality ||
                              n.xclk_hz != cfg.xclk_hz;
            bool need_cam = n.mode != MODE_INFERENCE_ONLY;
            if (n.score_thr != cfg.score_thr) det->set_score_thr(n.score_thr);
            if (n.mode != cfg.mode) {
                event_post(EV_INFO, "mode_changed", "%s -> %s", mode_name(cfg.mode), mode_name(n.mode));
                g_cnt.still_runs = 0;
                still_done_reported = false;
            }
            if (cam_on && (cam_params || !need_cam)) {
                camera_stop();
                cam_on = false;
            }
            if (need_cam && !cam_on) {
                cam_on = camera_start(n);
                if (cam_on)
                    event_post(EV_INFO, "camera_reconfigured", "frame=%s q=%u xclk=%u warmup=%u",
                               frame_size_name(n.frame_size), n.jpeg_quality, (unsigned)n.xclk_hz,
                               n.warmup_frames);
            }
            if (n.frame_size != cfg.frame_size) {
                // Coordinates change with frame size, so the old counts are not comparable.
                camera_fb_t *fb = cam_on ? esp_camera_fb_get() : nullptr;
                grid_reset(fb ? fb->width : 320, fb ? fb->height : 240);
                if (fb) esp_camera_fb_return(fb);
                event_post(EV_INFO, "grid_reset", "frame size changed to %s", frame_size_name(n.frame_size));
            }
            cfg = n;
        }

        if (cfg.mode == MODE_INFERENCE_ONLY) {
            if (g_cnt.still_runs < cfg.infer_only_runs) still_once(det);
            else {
                if (!still_done_reported) {
                    event_post(EV_INFO, "inference_only_done", "%u runs", (unsigned)cfg.infer_only_runs);
                    still_done_reported = true;
                }
                vTaskDelay(pdMS_TO_TICKS(50));
            }
        } else if (cam_on) {
            live_once(det, cfg, cap_index);
        } else {
            vTaskDelay(pdMS_TO_TICKS(500)); // camera failed to start; event already posted
        }
    }
}
