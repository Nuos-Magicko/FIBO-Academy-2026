/**
 * @file vision.cpp
 * The vision task (core 1): everything that happens to one camera frame on the board.
 *
 * For each frame (see process_camera_frame):
 *   1. wait for a picture from the camera                    (t_wait_frame)
 *   2. full_stream only: copy the JPEG into a bundle         (t_copy)
 *   3. decode the JPEG into RGB pixels                       (t_decode)
 *   4. run the AI: prepare the image, run the model, clean
 *      up the answer into boxes                              (t_pre, t_infer, t_post)
 *   5. add the boxes to the presence heatmap                 (t_grid)
 *   6. hand the bundle to the net task through the queue     (t_enqueue)
 * t_proc is the whole job, from step 1 ending to step 6 ending.
 *
 * Which steps run depends on the mode (camera_only, inference_only, detect_no_stream,
 * metadata_stream, full_stream). When the queue is full, the oldest waiting bundle is thrown away
 * (drop-oldest), so the AI never waits for the Wi-Fi.
 */
#include <cstring>
#include "vision/vision.h"
#include "config/settings.h"
#include "telemetry/events.h"
#include "telemetry/metrics.h"
#include "vision/bundle.h"
#include "vision/camera.h"
#include "vision/grid.h"
#include "dl_image_jpeg.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pedestrian_detect.hpp"

static const char *TAG = "vision";
static constexpr char MODEL_NAME[] = "pedestrian_detect_pico_s8_v1.espdl";
static constexpr uint32_t STEAL_OLDEST_WAIT_MS = 100;
static constexpr uint32_t CAPTURE_TIMEOUT_EVENT_EVERY = 100;
static constexpr uint32_t INFERENCE_DONE_IDLE_MS = 50;
static constexpr uint32_t CAMERA_FAILED_IDLE_MS = 500;
static constexpr uint16_t GRID_DEFAULT_WIDTH = 320, GRID_DEFAULT_HEIGHT = 240;

extern const uint8_t pedestrian_jpg_start[] asm("_binary_pedestrian_jpg_start");
extern const uint8_t pedestrian_jpg_end[] asm("_binary_pedestrian_jpg_end");

QueueHandle_t g_free_bundles, g_ready_bundles;
static bundle_t s_pool[BUNDLE_POOL_SIZE];
static uint32_t s_last_frame_id;

using stage_times_t = int32_t[ST_COUNT];
using detections_t = std::list<dl::detect::result_t>;

void pool_init()
{
    g_free_bundles = xQueueCreate(BUNDLE_POOL_SIZE, sizeof(bundle_t *));
    g_ready_bundles = xQueueCreate(READY_QUEUE_DEPTH, sizeof(bundle_t *));
    for (auto &bundle : s_pool) {
        bundle.jpeg_buffer = (uint8_t *)heap_caps_malloc(JPEG_BUFFER_BYTES, MALLOC_CAP_SPIRAM);
        bundle_t *pointer = &bundle;
        xQueueSend(g_free_bundles, &pointer, 0);
    }
}

/**
 * pedestrian_detect's Pico model with each stage timed separately. run_timed() repeats the three
 * calls of dl::detect::DetectImpl::run (esp-dl 3.3.13) with a timestamp between them: same model,
 * thresholds and postprocessor, no change to esp-dl. Re-check if esp-dl changes DetectImpl::run.
 */
class TimedPico : public pedestrian_detect::Pico {
public:
    explicit TimedPico(float score_threshold) : Pico(MODEL_NAME, score_threshold, DETECTOR_NMS_THRESHOLD) {}

    detections_t &run_timed(const dl::image::img_t &image, stage_times_t &stage_us)
    {
        int64_t started = esp_timer_get_time();
        m_image_preprocessor->preprocess(image);
        int64_t preprocessed = esp_timer_get_time();
        m_model->run();
        int64_t inferred = esp_timer_get_time();
        m_postprocessor->clear_result();
        m_postprocessor->postprocess();
        detections_t &detections = m_postprocessor->get_result(image.width, image.height);
        int64_t postprocessed = esp_timer_get_time();
        stage_us[ST_PRE] = preprocessed - started;
        stage_us[ST_INFER] = inferred - preprocessed;
        stage_us[ST_POST] = postprocessed - inferred;
        return detections;
    }
};

/** Everything the vision loop carries from one iteration to the next. */
struct vision_state_t {
    app_cfg_t cfg;
    uint32_t settings_generation;
    TimedPico *detector;
    bool camera_on;
    bool inference_only_done_reported;
    uint32_t capture_index;
};

/**
 * Gives IDLE1 one tick so the task watchdog stays fed. One tick is 1 ms because
 * CONFIG_FREERTOS_HZ=1000; it appears in the frame period but not in t_proc.
 */
static void yield_to_idle_task() { vTaskDelay(1); }

static int16_t clamp(int value, int lo, int hi) { return value < lo ? lo : value > hi ? hi : value; }

static void clear_stage_times(stage_times_t &stage_us)
{
    for (auto &duration : stage_us) duration = STAGE_NOT_MEASURED;
}

static void record_stage_times(const stage_times_t &stage_us)
{
    for (int stage = 0; stage < ST_COUNT; stage++)
        if (stage_us[stage] != STAGE_NOT_MEASURED) metrics_add((stage_t)stage, stage_us[stage]);
}

static dl::image::jpeg_img_t embedded_still()
{
    return {.data = (void *)pedestrian_jpg_start, .data_len = (size_t)(pedestrian_jpg_end - pedestrian_jpg_start)};
}

/**
 * Takes a free bundle. If none is free, takes the oldest queued bundle instead (drop-oldest) and
 * counts it in tx_dropped. Metadata modes first wait up to METADATA_ENQUEUE_WAIT_MS for a free one.
 */
static bundle_t *acquire_bundle(bool wait_for_free)
{
    bundle_t *bundle;
    TickType_t free_wait = wait_for_free ? pdMS_TO_TICKS(METADATA_ENQUEUE_WAIT_MS) : 0;
    if (xQueueReceive(g_free_bundles, &bundle, free_wait) == pdTRUE) return bundle;
    if (xQueueReceive(g_ready_bundles, &bundle, pdMS_TO_TICKS(STEAL_OLDEST_WAIT_MS)) == pdTRUE) {
        g_counters.tx_dropped++;
        return bundle;
    }
    return nullptr;
}

static void release_bundle(bundle_t *bundle) { xQueueSend(g_free_bundles, &bundle, 0); }

/** Queues the bundle for the net task, dropping the oldest queued bundle while the queue is full. */
static void enqueue_bundle(bundle_t *bundle, bool wait_for_space)
{
    if (wait_for_space && xQueueSend(g_ready_bundles, &bundle, pdMS_TO_TICKS(METADATA_ENQUEUE_WAIT_MS)) == pdTRUE)
        return;
    while (xQueueSend(g_ready_bundles, &bundle, 0) != pdTRUE) {
        bundle_t *oldest;
        if (xQueueReceive(g_ready_bundles, &oldest, 0) == pdTRUE) {
            release_bundle(oldest);
            g_counters.tx_dropped++;
        }
    }
}

/**
 * Copies stage times into the bundle, then queues it. The per-frame copy is taken before the
 * hand-off, so FRAME_TIMING cannot contain t_enqueue or t_proc; the METRICS window has both.
 */
static void hand_off_bundle(bundle_t *bundle, stage_times_t &stage_us, bool wait_for_space)
{
    memcpy(bundle->stage_us, stage_us, sizeof stage_us);
    int64_t started = esp_timer_get_time();
    enqueue_bundle(bundle, wait_for_space);
    stage_us[ST_ENQUEUE] = esp_timer_get_time() - started;
}

static void store_boxes(bundle_t *bundle, detections_t &detections, int width, int height)
{
    bundle->box_count = 0;
    for (auto &detection : detections) {
        if (bundle->box_count == MAX_BOXES_PER_FRAME) break;
        bundle->boxes[bundle->box_count++] = {clamp(detection.box[0], 0, width - 1), clamp(detection.box[1], 0, height - 1),
                                              clamp(detection.box[2], 0, width - 1), clamp(detection.box[3], 0, height - 1),
                                              detection.score};
    }
}

static dl::image::img_t decode_jpeg(const void *jpeg, size_t length, stage_times_t &stage_us)
{
    int64_t started = esp_timer_get_time();
    dl::image::jpeg_img_t source = {.data = (void *)jpeg, .data_len = length};
    dl::image::img_t image = dl::image::sw_decode_jpeg(source, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
    stage_us[ST_DECODE] = esp_timer_get_time() - started;
    return image;
}

/** inference_only: decode and detect the embedded still; send DETECTIONS without a JPEG. */
static void process_still_image(TimedPico *detector)
{
    stage_times_t stage_us;
    clear_stage_times(stage_us);
    int64_t started = esp_timer_get_time();
    dl::image::jpeg_img_t still = embedded_still();
    dl::image::img_t image = decode_jpeg(still.data, still.data_len, stage_us);
    if (!image.data) {
        g_counters.decode_fail++;
        return;
    }
    detections_t &detections = detector->run_timed(image, stage_us);
    bundle_t *bundle = acquire_bundle(true);
    if (bundle) {
        bundle->frame_id = ++s_last_frame_id;
        bundle->t_capture_us = started;
        bundle->width = image.width;
        bundle->height = image.height;
        bundle->jpeg_len = 0;
        store_boxes(bundle, detections, image.width, image.height);
        hand_off_bundle(bundle, stage_us, true);
    }
    stage_us[ST_PROC] = esp_timer_get_time() - started;
    heap_caps_free(image.data);
    g_counters.still_runs++;
    g_counters.frames_processed++;
    record_stage_times(stage_us);
}

/** Returns a frame, or nullptr after counting a capture timeout (event on the 1st and every 100th). */
static camera_fb_t *capture_frame(stage_times_t &stage_us, int64_t &captured_us)
{
    int64_t started = esp_timer_get_time();
    camera_fb_t *frame = esp_camera_fb_get();
    captured_us = esp_timer_get_time();
    stage_us[ST_WAIT_FRAME] = captured_us - started;
    if (frame) {
        g_counters.cap_ok++;
        return frame;
    }
    uint32_t timeouts = ++g_counters.cap_timeout;
    if (timeouts == 1 || timeouts % CAPTURE_TIMEOUT_EVENT_EVERY == 0)
        event_post(EV_WARN, "capture_timeout", "total=%u", (unsigned)timeouts);
    return nullptr;
}

/** Copies the JPEG into the bundle and returns the camera buffer to the driver at once. */
static bool copy_jpeg_and_release_frame(camera_fb_t *frame, bundle_t *bundle, stage_times_t &stage_us)
{
    if (frame->len > JPEG_BUFFER_BYTES) {
        g_counters.oversize++;
        esp_camera_fb_return(frame);
        return false;
    }
    int64_t started = esp_timer_get_time();
    memcpy(bundle->jpeg_buffer, frame->buf, frame->len);
    bundle->jpeg_len = frame->len;
    stage_us[ST_COPY] = esp_timer_get_time() - started;
    esp_camera_fb_return(frame);
    return true;
}

/** Modes without a JPEG copy decode straight from the camera buffer and return it afterwards. */
static dl::image::img_t decode_frame_and_release(camera_fb_t *frame, bundle_t *bundle, stage_times_t &stage_us)
{
    if (bundle) bundle->jpeg_len = 0;
    dl::image::img_t image = decode_jpeg(frame->buf, frame->len, stage_us);
    esp_camera_fb_return(frame);
    return image;
}

/** Live modes: process one camera frame. Only inferred frames are streamed, so overlays always match. */
static void process_camera_frame(vision_state_t &state)
{
    stage_times_t stage_us;
    clear_stage_times(stage_us);
    int64_t captured_us;
    camera_fb_t *frame = capture_frame(stage_us, captured_us);
    if (!frame) return;
    if (state.cfg.mode == MODE_CAMERA_ONLY) {
        esp_camera_fb_return(frame);
        metrics_add(ST_WAIT_FRAME, stage_us[ST_WAIT_FRAME]);
        return;
    }
    if (state.capture_index++ % state.cfg.infer_every_n) {
        esp_camera_fb_return(frame);
        g_counters.cadence_skipped++;
        return;
    }

    const bool full_stream = state.cfg.mode == MODE_FULL_STREAM;
    const bool metadata_stream = state.cfg.mode == MODE_METADATA_STREAM;
    const uint16_t width = frame->width, height = frame->height;
    bundle_t *bundle = nullptr;
    if (full_stream || metadata_stream) {
        bundle = acquire_bundle(metadata_stream);
        if (!bundle) {
            esp_camera_fb_return(frame);
            return;
        }
    }
    if (full_stream && !copy_jpeg_and_release_frame(frame, bundle, stage_us)) {
        release_bundle(bundle);
        return;
    }
    dl::image::img_t image = full_stream ? decode_jpeg(bundle->jpeg_buffer, bundle->jpeg_len, stage_us)
                                         : decode_frame_and_release(frame, bundle, stage_us);
    if (!image.data) {
        g_counters.decode_fail++;
        if (bundle) release_bundle(bundle);
        return;
    }
    detections_t &detections = state.detector->run_timed(image, stage_us);
    heap_caps_free(image.data);

    static bundle_t boxes_without_bundle;
    bundle_t *box_holder = bundle ? bundle : &boxes_without_bundle;
    store_boxes(box_holder, detections, width, height);
    int64_t grid_started = esp_timer_get_time();
    grid_add(box_holder->boxes, box_holder->box_count);
    stage_us[ST_GRID] = esp_timer_get_time() - grid_started;

    uint32_t frame_id = ++s_last_frame_id;
    if (bundle) {
        bundle->frame_id = frame_id;
        bundle->t_capture_us = captured_us;
        bundle->width = width;
        bundle->height = height;
        hand_off_bundle(bundle, stage_us, metadata_stream);
    }
    stage_us[ST_PROC] = esp_timer_get_time() - captured_us;
    g_counters.frames_processed++;
    record_stage_times(stage_us);
}

/** Runs the detector once on the embedded still and logs the boxes (expected values: docs/test_report.md). */
static void run_self_test(TimedPico *detector)
{
    stage_times_t stage_us;
    dl::image::jpeg_img_t still = embedded_still();
    dl::image::img_t image = dl::image::sw_decode_jpeg(still, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
    detections_t &detections = detector->run_timed(image, stage_us);
    for (auto &detection : detections)
        ESP_LOGI(TAG, "SELFTEST box score=%.3f x1=%d y1=%d x2=%d y2=%d", detection.score, detection.box[0],
                 detection.box[1], detection.box[2], detection.box[3]);
    event_post(EV_INFO, "self_test", "still %dx%d boxes=%u t_pre=%d t_infer=%d t_post=%d (first run)", image.width,
               image.height, (unsigned)detections.size(), (int)stage_us[ST_PRE], (int)stage_us[ST_INFER],
               (int)stage_us[ST_POST]);
    heap_caps_free(image.data);
}

/** Resets the grid to the size of an actual camera frame, or to QVGA when the camera is off. */
static void reset_grid_to_camera_frame(bool camera_on)
{
    camera_fb_t *frame = camera_on ? esp_camera_fb_get() : nullptr;
    grid_reset(frame ? frame->width : GRID_DEFAULT_WIDTH, frame ? frame->height : GRID_DEFAULT_HEIGHT);
    if (frame) esp_camera_fb_return(frame);
}

static bool camera_settings_differ(const app_cfg_t &a, const app_cfg_t &b)
{
    return a.frame_size != b.frame_size || a.jpeg_quality != b.jpeg_quality || a.xclk_hz != b.xclk_hz;
}

/** Restarts the camera when its settings change; the grid restarts when coordinates change. */
static void apply_new_settings(vision_state_t &state, const app_cfg_t &next)
{
    const bool camera_needed = next.mode != MODE_INFERENCE_ONLY;
    if (next.score_thr != state.cfg.score_thr) state.detector->set_score_thr(next.score_thr);
    if (next.mode != state.cfg.mode) {
        event_post(EV_INFO, "mode_changed", "%s -> %s", mode_name(state.cfg.mode), mode_name(next.mode));
        g_counters.still_runs = 0;
        state.inference_only_done_reported = false;
    }
    if (state.camera_on && (camera_settings_differ(next, state.cfg) || !camera_needed)) {
        camera_stop();
        state.camera_on = false;
    }
    if (camera_needed && !state.camera_on) {
        state.camera_on = camera_start(next);
        if (state.camera_on)
            event_post(EV_INFO, "camera_reconfigured", "frame=%s q=%u xclk=%u warmup=%u",
                       frame_size_name(next.frame_size), next.jpeg_quality, (unsigned)next.xclk_hz, next.warmup_frames);
    }
    if (next.frame_size != state.cfg.frame_size) {
        reset_grid_to_camera_frame(state.camera_on);
        event_post(EV_INFO, "grid_reset", "frame size changed to %s", frame_size_name(next.frame_size));
    }
    state.cfg = next;
}

static void run_inference_only(vision_state_t &state)
{
    if (g_counters.still_runs < state.cfg.infer_only_runs) {
        process_still_image(state.detector);
        return;
    }
    if (!state.inference_only_done_reported) {
        event_post(EV_INFO, "inference_only_done", "%u runs", (unsigned)state.cfg.infer_only_runs);
        state.inference_only_done_reported = true;
    }
    vTaskDelay(pdMS_TO_TICKS(INFERENCE_DONE_IDLE_MS));
}

void vision_task(void *)
{
    vision_state_t state = {};
    state.cfg = settings_get();
    state.settings_generation = settings_generation();
    state.detector = new TimedPico(state.cfg.score_thr);
    run_self_test(state.detector);
    state.camera_on = state.cfg.mode != MODE_INFERENCE_ONLY && camera_start(state.cfg);
    reset_grid_to_camera_frame(state.camera_on);

    for (;;) {
        yield_to_idle_task();
        if (settings_generation() != state.settings_generation) {
            state.settings_generation = settings_generation();
            apply_new_settings(state, settings_get());
        }
        if (state.cfg.mode == MODE_INFERENCE_ONLY) run_inference_only(state);
        else if (state.camera_on) process_camera_frame(state);
        else vTaskDelay(pdMS_TO_TICKS(CAMERA_FAILED_IDLE_MS));
    }
}
