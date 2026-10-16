"""The viewer: the window that shows what the board sees.

It also saves every measurement.

Connect to a board:
  python pc/main.py viewer --host 192.168.1.248
Play a saved session (no board needed):
  python pc/main.py viewer --replay samples/synthetic_run
Save one picture after 10 s, without opening a window:
  python pc/main.py viewer --host IP --snapshot out.png --snapshot-after 10

Keys:
  o  heatmap (none / presence / movement)
  r  clear heatmaps
  g  ask for the presence heatmap
  m  next mode
  s  save pictures on/off
  c  disconnect/reconnect
  q  quit (or Esc)

How it is built: a background thread receives the data and puts each record in
a queue; the main loop takes them out, passes them to the Pipeline
(pipeline.py), redraws the window when a new frame is complete, and once per
second writes a row of laptop measurements.

Measurements are always saved to runs/<date_time>_<board>/ (unless --no-log).
Camera pictures are saved only while saving is turned on with `s`, which is off
at the start, because the camera can see people.
"""

import argparse
import queue
import socket
import textwrap
import threading
import time
from typing import Any

import cv2
import numpy as np

from connection import protocol
from connection.clock_sync import host_us
from analysis.heatmap import LIMITATIONS, normalize_presence, overlay
from recording.logger import RunLogger
from analysis.pipeline import Pipeline
from recording.replay import paced, read_records

WINDOW_TITLE = "GMT people-flow"
OVERLAYS = ["none", "presence", "movement"]
PANEL_WIDTH = 420
VIEW_WIDTH = 640
MIN_CANVAS_HEIGHT = 640
FOOTER_WRAP_CHARS = 92
EVENT_LINE_CHARS = 50
PING_INTERVAL_S = 2
STATS_INTERVAL_S = 1
CONNECT_TIMEOUT_S = 3
RECONNECT_DELAY_S = 2
SOCKET_POLL_S = 0.5
NO_DATA_TIMEOUT_S = 10
"""The board sends at least a METRICS record every second and answers every
ping, so this long without data means the connection is dead (for example
after the Wi-Fi router went down), even if it was never closed."""
KEY_ESCAPE = 27

WHITE = (255, 255, 255)
GREY = (170, 170, 170)
LIGHT_GREY = (210, 210, 210)
GREEN = (0, 255, 0)
YELLOW = (0, 255, 255)
CYAN = (255, 255, 160)
RED = (0, 0, 255)
EVENT_COLOR = (180, 180, 255)
NOTE_COLOR = (0, 200, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX

Color = tuple[int, int, int]
"""A colour as (blue, green, red), the order OpenCV uses."""
QueueItem = tuple[str, protocol.Record | None, int]
"""What a source puts in the queue: (kind, record, nbytes), where kind is
"connected", "bytes" or "record"."""
SourceQueue = queue.Queue[QueueItem]


class LiveSource(threading.Thread):
    """Socket reader thread: bytes -> Parser -> queue of ("connected" | "bytes"
    | "record", record, nbytes)."""

    def __init__(self, host: str, port: int, out_queue: SourceQueue) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.queue = out_queue
        self.parser = protocol.Parser()
        self.want_connected = True
        self.sock: socket.socket | None = None
        self.last_data_time = time.monotonic()
        self.status = "connecting"
        self.running = True

    def send(self, command: dict[str, Any]) -> bool:
        """Send a COMMAND; returns False when not connected."""
        sock = self.sock
        if sock is None:
            return False
        try:
            sock.sendall(protocol.encode_command(command))
            return True
        except OSError:
            return False

    def run(self) -> None:
        while self.running:
            if not self.want_connected:
                self._disconnect("disconnected (press c)")
                time.sleep(0.1)
            elif self.sock is None:
                self._connect()
            else:
                self._receive()

    def _connect(self) -> None:
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=CONNECT_TIMEOUT_S
            )
            sock.settimeout(SOCKET_POLL_S)
            self.sock = sock
            self.last_data_time = time.monotonic()
            self.parser.buffer.clear()
            self.queue.put(("connected", None, 0))
            self.status = f"connected {self.host}:{self.port}"
        except OSError as error:
            self.status = (
                f"connect failed: {error.__class__.__name__}, retrying"
            )
            time.sleep(RECONNECT_DELAY_S)

    def _disconnect(self, status: str) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        self.status = status

    def _receive(self) -> None:
        sock = self.sock
        if sock is None:
            return
        try:
            data = sock.recv(65536)
            if not data:
                raise ConnectionError("closed by device")
        except socket.timeout:
            silent_s = time.monotonic() - self.last_data_time
            if silent_s > NO_DATA_TIMEOUT_S:
                self._disconnect(
                    f"no data for {NO_DATA_TIMEOUT_S} s, reconnecting"
                )
            return
        except OSError as error:
            self._disconnect(
                f"connection lost: {error.__class__.__name__}, retrying"
            )
            return
        self.last_data_time = time.monotonic()
        self.queue.put(("bytes", None, len(data)))
        for record in self.parser.feed(data):
            self.queue.put(("record", record, 0))


class ReplaySource(threading.Thread):
    """Feeds a recorded stream into the same queue as LiveSource. Commands are
    ignored."""

    def __init__(
        self, path: str, speed: float, out_queue: SourceQueue
    ) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.speed = speed
        self.queue = out_queue
        self.parser = protocol.Parser()
        self.status = f"replay {path}"
        self.want_connected = True
        self.running = True

    def send(self, command: dict[str, Any]) -> bool:
        return False

    def run(self) -> None:
        self.queue.put(("connected", None, 0))
        for record in paced(read_records(self.path), self.speed):
            if not self.running:
                return
            self.queue.put(("record", record, protocol.record_size(record)))
        self.status = f"replay finished: {self.path}"


def fmt(value: Any, decimals: int = 1, unit: str = "") -> str:
    """A number for the panel: floats rounded to `decimals`, "-" when there is
    no value."""
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.{decimals}f}{unit}"
    return f"{value}{unit}"


def fmt_ms(microseconds: float | None) -> str:
    if not microseconds:
        return fmt(None)
    return fmt(microseconds / 1000)


def frame_image(latest: dict[str, Any] | None) -> np.ndarray:
    """The latest JPEG decoded, or a placeholder when the mode sends no JPEG or
    it was not recorded."""
    image = None
    if latest is not None and latest["jpeg"]:
        image = cv2.imdecode(
            np.frombuffer(latest["jpeg"], np.uint8), cv2.IMREAD_COLOR
        )
    if image is None:
        if latest is not None:
            width = latest["w"]
            height = latest["h"]
            message = "no JPEG in this mode / not recorded"
        else:
            width = 320
            height = 240
            message = "waiting for frames"
        image = np.full((height, width, 3), 40, np.uint8)
        cv2.putText(
            image, message, (8, height // 2), FONT, 0.4, (200, 200, 200), 1
        )
    return image


def apply_overlay(
    view: np.ndarray, pipeline: Pipeline, overlay_name: str
) -> tuple[np.ndarray, str]:
    """Draw the selected heatmap; return (view, legend text with units)."""
    grid = pipeline.grid
    movement = pipeline.movement
    if overlay_name == "presence":
        if grid is None or not grid["frames"]:
            return view, "PRESENCE INTENSITY (sampled): no grid yet (press g)"
        view, highest = overlay(view, normalize_presence(grid, "frame"))
        return view, (
            "PRESENCE INTENSITY (sampled, device): detections per processed"
            f" frame per cell, max {highest:.3g}, over {grid['frames']} frames"
        )
    if overlay_name == "movement" and movement is not None:
        view, highest = overlay(view, movement.per_minute())
        return view, (
            "APPROXIMATE IMAGE-SPACE MOVEMENT (PC tracklets): step crossings"
            f" per minute per cell, max {highest:.3g},"
            f" over {movement.observed_s:.0f} s"
        )
    return view, "overlay: none"


def draw_detections(
    view: np.ndarray, latest: dict[str, Any], scale: float
) -> None:
    """Draw the boxes with scores, their bottom-center points, the frame_id."""
    for box in latest["boxes"]:
        top_left = (int(box["x1"] * scale), int(box["y1"] * scale))
        bottom_right = (int(box["x2"] * scale), int(box["y2"] * scale))
        cv2.rectangle(view, top_left, bottom_right, GREEN, 2)
        score_position = (top_left[0], max(top_left[1] - 4, 12))
        cv2.putText(
            view, f"{box['score']:.2f}", score_position, FONT, 0.5, GREEN, 1
        )
        feet = (int((box["x1"] + box["x2"]) / 2 * scale), bottom_right[1])
        cv2.circle(view, feet, 3, YELLOW, -1)
    label = f"frame_id {latest['frame_id']}"
    cv2.putText(view, label, (8, 22), FONT, 0.6, (0, 0, 0), 3)
    cv2.putText(view, label, (8, 22), FONT, 0.6, WHITE, 1)


def compose_canvas(view: np.ndarray, legend: str) -> np.ndarray:
    """The image on the left, the legend and limitations under it.

    The right side is left empty for the panel.
    """
    legend_lines = textwrap.wrap(legend, FOOTER_WRAP_CHARS)
    footer = legend_lines + textwrap.wrap(
        "Limits: " + LIMITATIONS, FOOTER_WRAP_CHARS
    )
    view_height = view.shape[0]
    canvas_height = max(view_height + 16 * len(footer) + 8, MIN_CANVAS_HEIGHT)
    canvas = np.zeros((canvas_height, VIEW_WIDTH + PANEL_WIDTH, 3), np.uint8)
    canvas[:view_height, :VIEW_WIDTH] = view
    for line_number, line in enumerate(footer):
        if line_number < len(legend_lines):
            color = CYAN
        else:
            color = GREY
        position = (6, view_height + 16 + 16 * line_number)
        cv2.putText(canvas, line, position, FONT, 0.38, color, 1)
    return canvas


def clock_line(stats: dict[str, Any]) -> str:
    clock_uncertainty = stats.get("clock_uncertainty_us")
    if clock_uncertainty is None or clock_uncertainty == "":
        return "clock sync: no PONG yet"
    uncertainty_ms = clock_uncertainty / 1000
    return (
        f"clock sync +-{uncertainty_ms:.1f} ms"
        " (min-RTT/2, raw offset in host_stats.csv)"
    )


def capture_to_display_line(ui: dict[str, Any]) -> str:
    capture_to_display = ui.get("capture_to_display")
    if not capture_to_display:
        return "capture->display: n/a (needs PONG)"
    estimate_ms, uncertainty_ms = capture_to_display
    return f"capture->display ~{estimate_ms:.0f} ms +-{uncertainty_ms:.0f} ms"


def panel_lines(
    pipeline: Pipeline, source: LiveSource | ReplaySource, ui: dict[str, Any]
) -> list[tuple[str, Color | None]]:
    """The (text, color) lines of the right-hand panel.

    A color of None means the default grey.
    """
    hello = pipeline.hello
    if hello is None:
        hello = {}
    config = hello.get("config", {})
    metrics = pipeline.metrics
    if metrics is None:
        metrics = {}
    stats = ui["stats"]

    device_id = hello.get("device_id", "-")
    firmware = hello.get("fw_version", "-")
    sensor = hello.get("sensor_pid", "-")
    idf = hello.get("idf", "-")
    mode = config.get("mode", "-")
    frame_size = config.get("frame_size", "-")
    quality = config.get("jpeg_quality", "-")
    every = config.get("infer_every_n", "-")

    fps_processed = fmt(metrics.get("fps_processed"))
    fps_sent = fmt(metrics.get("fps_sent"))
    infer_mean = fmt_ms(metrics.get("stages.t_infer.mean"))
    proc_p95 = fmt_ms(metrics.get("stages.t_proc.p95"))
    send_mean = fmt_ms(metrics.get("stages.t_send.mean"))
    tx_dropped = fmt(metrics.get("counters.tx_dropped"))
    temperature = fmt(metrics.get("temp_c"))
    rssi = fmt(metrics.get("rssi"))
    int_min = fmt(metrics.get("mem.int_min"))

    rx_frames = fmt(stats.get("rx_pairs_per_s"))
    rx_kilobytes = fmt(stats.get("rx_bytes_per_s", 0) / 1000)
    display_fps = fmt(ui.get("display_fps"))
    display_ms = fmt(ui.get("display_ms"))
    seq_gaps = pipeline.stats["seq_gaps"]
    crc_errors = source.parser.crc_errors
    missing_pairs = pipeline.stats["missing_pairs"]
    frame_id_gaps = pipeline.stats["frame_id_gaps"]

    lines = [
        ("GMT people-flow viewer", WHITE),
        (source.status, YELLOW),
        ("", None),
        (f"device {device_id}  fw {firmware}", None),
        (f"sensor {sensor}  idf {idf}", None),
        (f"mode {mode}  {frame_size} q{quality}  every {every}", None),
        ("", None),
        ("DEVICE (last METRICS window)", WHITE),
        (f"processed {fps_processed} fps   sent {fps_sent} fps", None),
        (f"t_infer mean {infer_mean} ms   t_proc p95 {proc_p95} ms", None),
        (f"t_send mean {send_mean} ms   tx_dropped {tx_dropped}", None),
        (f"temp {temperature} C   rssi {rssi}   int_min {int_min} B", None),
        ("", None),
        ("HOST", WHITE),
        (f"rx {rx_frames} frames/s   {rx_kilobytes} kB/s", None),
        (f"display {display_fps} fps   draw {display_ms} ms", None),
        (
            f"seq gaps {seq_gaps}  crc {crc_errors}"
            f"  missing pairs {missing_pairs}",
            None,
        ),
        (f"frame_id gaps (device drops) {frame_id_gaps}", None),
        (clock_line(stats), None),
        (capture_to_display_line(ui), None),
        ("", None),
        ("EVENTS", WHITE),
    ]
    recent_events = list(pipeline.events)[-5:]
    for event in recent_events:
        lines.append((event[:EVENT_LINE_CHARS], EVENT_COLOR))

    if ui["recording"]:
        recording = "ON"
    else:
        recording = "off"
    lines.append(("", None))
    lines.append(
        (f"overlay [o] {ui['overlay']}   recording [s] {recording}", WHITE)
    )
    lines.append(
        ("[r] reset grids [g] get grid [m] mode [c] connect [q] quit", GREY)
    )
    if ui.get("note"):
        lines.append((ui["note"], NOTE_COLOR))
    return lines


def render(
    pipeline: Pipeline, source: LiveSource | ReplaySource, ui: dict[str, Any]
) -> np.ndarray:
    """Draw the whole window and store the drawing time in ui["display_ms"]."""
    started = time.perf_counter()
    latest = pipeline.latest
    image = frame_image(latest)
    scale = VIEW_WIDTH / image.shape[1]
    view = cv2.resize(
        image,
        (VIEW_WIDTH, int(image.shape[0] * scale)),
        interpolation=cv2.INTER_LINEAR,
    )
    view, legend = apply_overlay(view, pipeline, ui["overlay"])
    if latest is not None:
        draw_detections(view, latest, scale)
    if ui["recording"]:
        cv2.circle(view, (VIEW_WIDTH - 16, 16), 7, RED, -1)
    canvas = compose_canvas(view, legend)
    for line_number, line in enumerate(panel_lines(pipeline, source, ui)):
        text, color = line
        if color is None:
            color = LIGHT_GREY
        cv2.putText(
            canvas,
            text,
            (VIEW_WIDTH + 10, 22 + 19 * line_number),
            FONT,
            0.45,
            color,
            1,
        )
    ui["display_ms"] = (time.perf_counter() - started) * 1000
    return canvas


class Viewer:
    """Main loop: drain the source queue, ping, redraw on new frames, log host
    stats, handle keys."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.queue: SourceQueue = queue.Queue()
        self.source: LiveSource | ReplaySource
        self.logger: RunLogger | None
        if args.host:
            self.source = LiveSource(args.host, args.port, self.queue)
        else:
            self.source = ReplaySource(args.replay, args.speed, self.queue)
        if args.no_log:
            self.logger = None
        else:
            self.logger = RunLogger(recording=args.record)
        self.pipeline = Pipeline(logger=self.logger)
        self.ui = {
            "overlay": args.overlay,
            "recording": args.record,
            "stats": {},
            "note": "",
        }
        self.headless = bool(args.snapshot)
        self.canvas: np.ndarray | None = None
        self.frames_drawn = 0
        self.last_drawn_frame_id = None
        self.fps_window_start = time.monotonic()

    def run(self) -> None:
        if not self.headless:
            cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_AUTOSIZE)
        self.source.start()
        started = time.monotonic()
        next_stats = started
        next_ping = started
        try:
            while True:
                self.drain_queue()
                now = time.monotonic()
                if self.args.host and now >= next_ping:
                    self.source.send(self.pipeline.clock.ping_payload())
                    next_ping = now + PING_INTERVAL_S
                stats_due = now >= next_stats
                self.redraw_if_needed(stats_due)
                if stats_due:
                    self.log_host_stats(now)
                    next_stats = now + STATS_INTERVAL_S
                if self.headless:
                    if now - started >= self.args.snapshot_after:
                        self.save_snapshot()
                        break
                    time.sleep(0.01)
                else:
                    keep_running = self.show_and_handle_key()
                    if not keep_running:
                        break
        finally:
            self.source.running = False
            if self.logger is not None:
                self.logger.close()
            if not self.headless:
                cv2.destroyAllWindows()

    def drain_queue(self) -> None:
        while True:
            try:
                kind, record, nbytes = self.queue.get_nowait()
            except queue.Empty:
                return
            if kind == "connected":
                self.pipeline.new_connection()
            elif kind == "bytes":
                self.pipeline.stats["bytes"] += nbytes
            elif record is not None:
                self.pipeline.feed(record, nbytes)

    def redraw_if_needed(self, stats_due: bool) -> None:
        latest = self.pipeline.latest
        new_frame = (
            latest is not None
            and latest["frame_id"] != self.last_drawn_frame_id
        )
        if self.canvas is not None and not new_frame and not stats_due:
            return
        self.canvas = render(self.pipeline, self.source, self.ui)
        if new_frame and latest is not None:
            self.last_drawn_frame_id = latest["frame_id"]
            self.frames_drawn += 1
            self.update_capture_to_display(latest)

    def update_capture_to_display(self, latest: dict[str, Any]) -> None:
        """Only for live JPEG frames: replayed or metadata frames have no
        meaningful display time."""
        if latest["t_capture_us"] is None or not self.args.host:
            return
        estimate = self.pipeline.clock.capture_to_host_us(
            latest["t_capture_us"], host_us()
        )
        if estimate is None:
            self.ui["capture_to_display"] = None
        else:
            self.ui["capture_to_display"] = (
                estimate[0] / 1000,
                estimate[1] / 1000,
            )

    def log_host_stats(self, now: float) -> None:
        self.ui["display_fps"] = self.frames_drawn / max(
            now - self.fps_window_start, 1e-3
        )
        self.frames_drawn = 0
        self.fps_window_start = now
        capture_to_display_ms = ""
        capture_to_display_unc_ms = ""
        capture_to_display = self.ui.get("capture_to_display")
        if capture_to_display:
            capture_to_display_ms = round(capture_to_display[0], 1)
            capture_to_display_unc_ms = round(capture_to_display[1], 1)
        viewer_columns = {
            "display_fps": round(self.ui["display_fps"], 2),
            "display_ms": round(self.ui.get("display_ms", 0), 2),
            "capture_to_display_ms": capture_to_display_ms,
            "capture_to_display_unc_ms": capture_to_display_unc_ms,
        }
        self.ui["stats"] = self.pipeline.host_stats(
            self.source.parser, viewer_columns
        )

    def save_snapshot(self) -> None:
        if self.canvas is None:
            return
        cv2.imwrite(self.args.snapshot, self.canvas)
        log_dir = None
        if self.logger is not None:
            log_dir = self.logger.dir
        print(
            f"snapshot -> {self.args.snapshot};"
            f" stats {self.pipeline.stats}; log {log_dir}"
        )

    def show_and_handle_key(self) -> bool:
        """Returns False when the user quits or closes the window."""
        if self.canvas is None:
            return True
        cv2.imshow(WINDOW_TITLE, self.canvas)
        key = cv2.waitKey(15) & 0xFF
        if key == ord("q") or key == KEY_ESCAPE:
            return False
        if cv2.getWindowProperty(WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
            return False
        if key == ord("o"):
            self.cycle_overlay()
        elif key == ord("r"):
            self.reset_grids()
        elif key == ord("g"):
            self.request_grid()
        elif key == ord("m"):
            self.next_mode()
        elif key == ord("s"):
            self.toggle_recording()
        elif key == ord("c"):
            self.toggle_connection()
        return True

    def cycle_overlay(self) -> None:
        position = OVERLAYS.index(self.ui["overlay"])
        next_position = (position + 1) % len(OVERLAYS)
        self.ui["overlay"] = OVERLAYS[next_position]

    def reset_grids(self) -> None:
        self.pipeline.reset_movement()
        self.pipeline.grid = None
        sent = self.source.send({"cmd": "reset_grid"})
        if sent:
            self.ui["note"] = "grids reset"
        else:
            self.ui["note"] = "PC grid reset (no device)"

    def request_grid(self) -> None:
        sent = self.source.send({"cmd": "get_grid"})
        if sent:
            self.ui["note"] = "grid requested"
        else:
            self.ui["note"] = "no device connected"

    def next_mode(self) -> None:
        current = self.pipeline.mode
        if current is None:
            current = protocol.MODES[-1]
        if current in protocol.MODES:
            next_position = (protocol.MODES.index(current) + 1) % len(
                protocol.MODES
            )
            following = protocol.MODES[next_position]
        else:
            following = protocol.MODES[0]
        sent = self.source.send({"cmd": "set", "values": {"mode": following}})
        if sent:
            self.ui["note"] = f"mode -> {following}"
        else:
            self.ui["note"] = "no device connected"

    def toggle_recording(self) -> None:
        self.ui["recording"] = not self.ui["recording"]
        if self.logger is not None:
            self.logger.recording = self.ui["recording"]
        if self.ui["recording"]:
            self.ui["note"] = "recording ON: JPEGs saved to the run folder"
        else:
            self.ui["note"] = "recording off"

    def toggle_connection(self) -> None:
        self.source.want_connected = not self.source.want_connected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--host")
    source.add_argument("--replay", help="run directory or .gmtp file")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="replay speed (0 = as fast as possible)",
    )
    parser.add_argument(
        "--record", action="store_true", help="start with JPEG recording on"
    )
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument(
        "--snapshot", help="headless: save the view to this PNG and exit"
    )
    parser.add_argument("--snapshot-after", type=float, default=10.0)
    parser.add_argument("--overlay", choices=OVERLAYS, default="none")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    viewer = Viewer(args)
    viewer.run()
    return 0
