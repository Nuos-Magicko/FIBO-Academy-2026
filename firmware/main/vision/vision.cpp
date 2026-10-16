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
 * (drop-oldest); the metadata modes first wait up to METADATA_ENQUEUE_WAIT_MS for space.
 */
#include <algorithm>
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
static constexpr TickType_t IDLE_YIELD_TICKS = 1;
static constexpr TickType_t NO_WAIT = 0;

extern const uint8_t pedestrian_jpg_start[] asm("_binary_pedestrian_jpg_start");
extern const uint8_t pedestrian_jpg_end[] asm("_binary_pedestrian_jpg_end");

QueueHandle_t g_free_bundles;
QueueHandle_t g_ready_bundles;
static bundle_t s_pool[BUNDLE_POOL_SIZE];
static uint32_t s_last_frame_id;

/** The list of boxes the detector returns. */
using detections_t = std::list<dl::detect::result_t>;

void pool_init()
{
    g_free_bundles = xQueueCreate(BUNDLE_POOL_SIZE, sizeof(bundle_t *));
    g_ready_bundles = xQueueCreate(READY_QUEUE_DEPTH, sizeof(bundle_t *));
    for (int index = 0; index < BUNDLE_POOL_SIZE; index++) {
        bundle_t *bundle = &s_pool[index];
        void *memory = heap_caps_malloc(JPEG_BUFFER_BYTES, MALLOC_CAP_SPIRAM);
        bundle->jpeg_buffer = static_cast<uint8_t *>(memory);
        xQueueSend(g_free_bundles, &bundle, NO_WAIT);
    }
}

/**
 * pedestrian_detect's Pico model with each stage timed separately. run_timed() repeats the three
 * calls of dl::detect::DetectImpl::run (esp-dl 3.3.13) with a timestamp between them: same model,
 * thresholds and postprocessor, no change to esp-dl. Re-check if esp-dl changes DetectImpl::run.
 */
class TimedPico : public pedestrian_detect::Pico {
public:
    explicit TimedPico(float score_threshold) : Pico(MODEL_NAME, score_threshold, DETECTOR_NMS_THRESHOLD)
    {
    }

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
        stage_us[stage_t::pre] = preprocessed - started;
        stage_us[stage_t::infer] = inferred - preprocessed;
        stage_us[stage_t::post] = postprocessed - inferred;
        return detections;
    }
};

/** Everything the vision loop carries from one iteration to the next. */
struct vision_state_t {
    app_cfg_t settings;
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
static void yield_to_idle_task()
{
    vTaskDelay(IDLE_YIELD_TICKS);
}

static void record_stage_times(const stage_times_t &stage_us)
{
    for (stage_t stage : ALL_STAGES) {
        if (stage_us[stage] != STAGE_NOT_MEASURED) {
            metrics_add(stage, stage_us[stage]);
        }
    }
}

static dl::image::jpeg_img_t embedded_still()
{
    dl::image::jpeg_img_t still;
    still.data = const_cast<uint8_t *>(pedestrian_jpg_start);
    still.data_len = static_cast<size_t>(pedestrian_jpg_end - pedestrian_jpg_start);
    return still;
}

/**
 * Takes a free bundle. If none is free, takes the oldest queued bundle instead (drop-oldest) and
 * counts it in tx_dropped. Metadata modes first wait up to METADATA_ENQUEUE_WAIT_MS for a free one.
 */
static bundle_t *acquire_bundle(bool wait_for_free)
{
    bundle_t *bundle;
    TickType_t free_wait = NO_WAIT;
    if (wait_for_free) {
        free_wait = pdMS_TO_TICKS(METADATA_ENQUEUE_WAIT_MS);
    }
    if (xQueueReceive(g_free_bundles, &bundle, free_wait) == pdTRUE) {
        return bundle;
    }
    if (xQueueReceive(g_ready_bundles, &bundle, pdMS_TO_TICKS(STEAL_OLDEST_WAIT_MS)) == pdTRUE) {
        g_counters.tx_dropped++;
        return bundle;
    }
    return nullptr;
}

static void release_bundle(bundle_t *bundle)
{
    xQueueSend(g_free_bundles, &bundle, NO_WAIT);
}

/** Queues the bundle for the net task, dropping the oldest queued bundle while the queue is full. */
static void enqueue_bundle(bundle_t *bundle, bool wait_for_space)
{
    if (wait_for_space) {
        if (xQueueSend(g_ready_bundles, &bundle, pdMS_TO_TICKS(METADATA_ENQUEUE_WAIT_MS)) == pdTRUE) {
            return;
        }
    }
    while (xQueueSend(g_ready_bundles, &bundle, NO_WAIT) != pdTRUE) {
        bundle_t *oldest;
        if (xQueueReceive(g_ready_bundles, &oldest, NO_WAIT) == pdTRUE) {
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
    bundle->stage_us = stage_us;
    int64_t started = esp_timer_get_time();
    enqueue_bundle(bundle, wait_for_space);
    stage_us[stage_t::enqueue] = esp_timer_get_time() - started;
}

static void store_boxes(bundle_t *bundle, detections_t &detections, int width, int height)
{
    bundle->box_count = 0;
    for (const dl::detect::result_t &detection : detections) {
        if (bundle->box_count == MAX_BOXES_PER_FRAME) {
            break;
        }
        box_t &box = bundle->boxes[bundle->box_count];
        box.x1 = static_cast<int16_t>(std::clamp(detection.box[0], 0, width - 1));
        box.y1 = static_cast<int16_t>(std::clamp(detection.box[1], 0, height - 1));
        box.x2 = static_cast<int16_t>(std::clamp(detection.box[2], 0, width - 1));
        box.y2 = static_cast<int16_t>(std::clamp(detection.box[3], 0, height - 1));
        box.score = detection.score;
        bundle->box_count++;
    }
}

static dl::image::img_t decode_jpeg(const void *jpeg, size_t length, stage_times_t &stage_us)
{
    int64_t started = esp_timer_get_time();
    dl::image::jpeg_img_t source;
    source.data = const_cast<void *>(jpeg);
    source.data_len = length;
    dl::image::img_t image = dl::image::sw_decode_jpeg(source, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
    stage_us[stage_t::decode] = esp_timer_get_time() - started;
    return image;
}

static uint32_t next_frame_id()
{
    s_last_frame_id++;
    return s_last_frame_id;
}

/** inference_only: decode and detect the embedded still; send DETECTIONS without a JPEG. */
static void process_still_image(TimedPico *detector)
{
    stage_times_t stage_us;
    stage_us.clear();
    int64_t started = esp_timer_get_time();
    dl::image::jpeg_img_t still = embedded_still();
    dl::image::img_t image = decode_jpeg(still.data, still.data_len, stage_us);
    if (image.data == nullptr) {
        g_counters.decode_fail++;
        return;
    }
    detections_t &detections = detector->run_timed(image, stage_us);
    bundle_t *bundle = acquire_bundle(true);
    if (bundle != nullptr) {
        bundle->frame_id = next_frame_id();
        bundle->t_capture_us = started;
        bundle->width = image.width;
        bundle->height = image.height;
        bundle->jpeg_len = 0;
        store_boxes(bundle, detections, image.width, image.height);
        hand_off_bundle(bundle, stage_us, true);
    }
    stage_us[stage_t::proc] = esp_timer_get_time() - started;
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
    stage_us[stage_t::wait_frame] = captured_us - started;
    if (frame != nullptr) {
        g_counters.cap_ok++;
        return frame;
    }
    g_counters.cap_timeout++;
    uint32_t timeouts = g_counters.cap_timeout.load();
    if (timeouts == 1 || timeouts % CAPTURE_TIMEOUT_EVENT_EVERY == 0) {
        event_post(event_severity_t::warn, "capture_timeout", "total=%u", static_cast<unsigned>(timeouts));
    }
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
    stage_us[stage_t::copy] = esp_timer_get_time() - started;
    esp_camera_fb_return(frame);
    return true;
}

/** Modes without a JPEG copy decode straight from the camera buffer and return it afterwards. */
static dl::image::img_t decode_frame_and_release(camera_fb_t *frame, bundle_t *bundle, stage_times_t &stage_us)
{
    if (bundle != nullptr) {
        bundle->jpeg_len = 0;
    }
    dl::image::img_t image = decode_jpeg(frame->buf, frame->len, stage_us);
    esp_camera_fb_return(frame);
    return image;
}

/** True for the frames the detector should skip (infer_every_n: only every n-th frame is processed). */
static bool skip_for_cadence(vision_state_t &state)
{
    bool skip = (state.capture_index % state.settings.infer_every_n != 0);
    state.capture_index++;
    return skip;
}

/** Live modes: process one camera frame. Only inferred frames are streamed, so overlays always match. */
static void process_camera_frame(vision_state_t &state)
{
    stage_times_t stage_us;
    stage_us.clear();
    int64_t captured_us;
    camera_fb_t *frame = capture_frame(stage_us, captured_us);
    if (frame == nullptr) {
        return;
    }
    if (state.settings.mode == app_mode_t::camera_only) {
        esp_camera_fb_return(frame);
        metrics_add(stage_t::wait_frame, stage_us[stage_t::wait_frame]);
        return;
    }
    if (skip_for_cadence(state)) {
        esp_camera_fb_return(frame);
        g_counters.cadence_skipped++;
        return;
    }

    const bool full_stream = (state.settings.mode == app_mode_t::full_stream);
    const bool metadata_stream = (state.settings.mode == app_mode_t::metadata_stream);
    const uint16_t width = frame->width;
    const uint16_t height = frame->height;
    bundle_t *bundle = nullptr;
    if (full_stream || metadata_stream) {
        bundle = acquire_bundle(metadata_stream);
        if (bundle == nullptr) {
            esp_camera_fb_return(frame);
            return;
        }
    }

    dl::image::img_t image;
    if (full_stream) {
        if (!copy_jpeg_and_release_frame(frame, bundle, stage_us)) {
            release_bundle(bundle);
            return;
        }
        image = decode_jpeg(bundle->jpeg_buffer, bundle->jpeg_len, stage_us);
    } else {
        image = decode_frame_and_release(frame, bundle, stage_us);
    }
    if (image.data == nullptr) {
        g_counters.decode_fail++;
        if (bundle != nullptr) {
            release_bundle(bundle);
        }
        return;
    }
    detections_t &detections = state.detector->run_timed(image, stage_us);
    heap_caps_free(image.data);

    static bundle_t boxes_without_bundle;
    bundle_t *box_holder = &boxes_without_bundle;
    if (bundle != nullptr) {
        box_holder = bundle;
    }
    store_boxes(box_holder, detections, width, height);
    int64_t grid_started = esp_timer_get_time();
    grid_add(box_holder->boxes.data(), box_holder->box_count);
    stage_us[stage_t::grid] = esp_timer_get_time() - grid_started;

    uint32_t frame_id = next_frame_id();
    if (bundle != nullptr) {
        bundle->frame_id = frame_id;
        bundle->t_capture_us = captured_us;
        bundle->width = width;
        bundle->height = height;
        hand_off_bundle(bundle, stage_us, metadata_stream);
    }
    stage_us[stage_t::proc] = esp_timer_get_time() - captured_us;
    g_counters.frames_processed++;
    record_stage_times(stage_us);
}

/** Runs the detector once on the embedded still and logs the boxes (verify-board expects 3). */
static void run_self_test(TimedPico *detector)
{
    stage_times_t stage_us;
    stage_us.clear();
    dl::image::jpeg_img_t still = embedded_still();
    dl::image::img_t image = dl::image::sw_decode_jpeg(still, dl::image::DL_IMAGE_PIX_TYPE_RGB888);
    detections_t &detections = detector->run_timed(image, stage_us);
    for (const dl::detect::result_t &detection : detections) {
        ESP_LOGI(TAG, "SELFTEST box score=%.3f x1=%d y1=%d x2=%d y2=%d", detection.score, detection.box[0],
                 detection.box[1], detection.box[2], detection.box[3]);
    }
    event_post(event_severity_t::info, "self_test", "still %dx%d boxes=%u t_pre=%d t_infer=%d t_post=%d (first run)",
               image.width, image.height, static_cast<unsigned>(detections.size()),
               static_cast<int>(stage_us[stage_t::pre]), static_cast<int>(stage_us[stage_t::infer]),
               static_cast<int>(stage_us[stage_t::post]));
    heap_caps_free(image.data);
}

/** Resets the grid to the size of an actual camera frame, or to QVGA when the camera is off. */
static void reset_grid_to_camera_frame(bool camera_on)
{
    camera_fb_t *frame = nullptr;
    if (camera_on) {
        frame = esp_camera_fb_get();
    }
    if (frame != nullptr) {
        grid_reset(frame->width, frame->height);
        esp_camera_fb_return(frame);
    } else {
        grid_reset(GRID_DEFAULT_FRAME_WIDTH, GRID_DEFAULT_FRAME_HEIGHT);
    }
}

static bool camera_settings_differ(const app_cfg_t &first, const app_cfg_t &second)
{
    if (first.frame_size != second.frame_size) {
        return true;
    }
    if (first.jpeg_quality != second.jpeg_quality) {
        return true;
    }
    if (first.xclk_hz != second.xclk_hz) {
        return true;
    }
    return false;
}

/** Restarts the camera when its settings change; the grid restarts when coordinates change. */
static void apply_new_settings(vision_state_t &state, const app_cfg_t &next)
{
    const bool camera_needed = (next.mode != app_mode_t::inference_only);
    if (next.score_thr != state.settings.score_thr) {
        state.detector->set_score_thr(next.score_thr);
    }
    if (next.mode != state.settings.mode) {
        event_post(event_severity_t::info, "mode_changed", "%s -> %s", mode_name(state.settings.mode), mode_name(next.mode));
        g_counters.still_runs = 0;
        state.inference_only_done_reported = false;
    }
    if (state.camera_on) {
        if (camera_settings_differ(next, state.settings) || !camera_needed) {
            camera_stop();
            state.camera_on = false;
        }
    }
    if (camera_needed && !state.camera_on) {
        state.camera_on = camera_start(next);
        if (state.camera_on) {
            event_post(event_severity_t::info, "camera_reconfigured", "frame=%s q=%u xclk=%u warmup=%u",
                       frame_size_name(next.frame_size), next.jpeg_quality, static_cast<unsigned>(next.xclk_hz),
                       next.warmup_frames);
        }
    }
    if (next.frame_size != state.settings.frame_size) {
        reset_grid_to_camera_frame(state.camera_on);
        event_post(event_severity_t::info, "grid_reset", "frame size changed to %s", frame_size_name(next.frame_size));
    }
    state.settings = next;
}

static void run_inference_only(vision_state_t &state)
{
    if (g_counters.still_runs < state.settings.infer_only_runs) {
        process_still_image(state.detector);
        return;
    }
    if (!state.inference_only_done_reported) {
        event_post(event_severity_t::info, "inference_only_done", "%u runs",
                   static_cast<unsigned>(state.settings.infer_only_runs));
        state.inference_only_done_reported = true;
    }
    vTaskDelay(pdMS_TO_TICKS(INFERENCE_DONE_IDLE_MS));
}

void vision_task(void *)
{
    vision_state_t state = {};
    state.settings = settings_get();
    state.settings_generation = settings_generation();
    static TimedPico detector(state.settings.score_thr);
    state.detector = &detector;
    run_self_test(state.detector);
    state.camera_on = false;
    if (state.settings.mode != app_mode_t::inference_only) {
        state.camera_on = camera_start(state.settings);
    }
    reset_grid_to_camera_frame(state.camera_on);

    while (true) {
        yield_to_idle_task();
        if (settings_generation() != state.settings_generation) {
            state.settings_generation = settings_generation();
            apply_new_settings(state, settings_get());
        }
        if (state.settings.mode == app_mode_t::inference_only) {
            run_inference_only(state);
        } else if (state.camera_on) {
            process_camera_frame(state);
        } else {
            vTaskDelay(pdMS_TO_TICKS(CAMERA_FAILED_IDLE_MS));
        }
    }
}
