"""The viewer: the window that shows what the board sees, and saves every measurement.

  python pc/main.py viewer --host 192.168.1.248             connect to a board
  python pc/main.py viewer --replay samples/synthetic_run   play a saved session (no board needed)
  python pc/main.py viewer --host IP --snapshot out.png --snapshot-after 10   save one picture, no window

Keys: o heatmap (none / presence / movement)   r clear heatmaps   g ask for the presence heatmap
      m next mode   s save pictures on/off   c disconnect/reconnect   q or Esc quit

How it is built: a background thread receives the data and puts each record in a queue; the
main loop takes them out, passes them to the Pipeline (pipeline.py), redraws the window when a
new frame is complete, and once per second writes a row of laptop measurements.

Measurements are always saved to runs/<date_time>_<board>/ (unless --no-log). Camera pictures
are saved only while saving is turned on with `s`, which is off at the start, because the camera
can see people.
"""
import argparse
import queue
import socket
import textwrap
import threading
import time

import cv2
import numpy as np

from connection import protocol as P
from connection.clock_sync import host_us
from analysis.heatmap import LIMITATIONS, normalize_presence, overlay
from recording.logger import RunLogger
from analysis.pipeline import Pipeline
from recording.replay import paced, read_records

WINDOW_TITLE = "GMT people-flow"
MODES = ["full_stream", "metadata_stream", "detect_no_stream", "camera_only", "inference_only"]
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
KEY_ESCAPE = 27

WHITE, GREY, LIGHT_GREY = (255, 255, 255), (170, 170, 170), (210, 210, 210)
GREEN, YELLOW, CYAN, RED = (0, 255, 0), (0, 255, 255), (255, 255, 160), (0, 0, 255)
EVENT_COLOR, NOTE_COLOR = (180, 180, 255), (0, 200, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX


class LiveSource(threading.Thread):
    """Socket reader thread: bytes -> Parser -> queue of ("connected" | "bytes" | "record", record, nbytes)."""

    def __init__(self, host, port, out_queue):
        super().__init__(daemon=True)
        self.host, self.port, self.queue = host, port, out_queue
        self.parser = P.Parser()
        self.want_connected = True
        self.sock = None
        self.status = "connecting"
        self.running = True

    def send(self, command):
        """Send a COMMAND; returns False when not connected."""
        sock = self.sock
        if sock is None:
            return False
        try:
            sock.sendall(P.encode_command(command))
            return True
        except OSError:
            return False

    def run(self):
        while self.running:
            if not self.want_connected:
                self._disconnect("disconnected (press c)")
                time.sleep(0.1)
            elif self.sock is None:
                self._connect()
            else:
                self._receive()

    def _connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT_S)
            self.sock.settimeout(SOCKET_POLL_S)
            self.parser.buffer.clear()
            self.queue.put(("connected", None, 0))
            self.status = f"connected {self.host}:{self.port}"
        except OSError as error:
            self.status = f"connect failed: {error.__class__.__name__}, retrying"
            time.sleep(RECONNECT_DELAY_S)

    def _disconnect(self, status):
        if self.sock:
            self.sock.close()
            self.sock = None
        self.status = status

    def _receive(self):
        try:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("closed by device")
        except socket.timeout:
            return
        except OSError as error:
            self._disconnect(f"connection lost: {error.__class__.__name__}, retrying")
            return
        self.queue.put(("bytes", None, len(data)))
        for record in self.parser.feed(data):
            self.queue.put(("record", record, 0))


class ReplaySource(threading.Thread):
    """Feeds a recorded stream into the same queue as LiveSource. Commands are ignored."""

    def __init__(self, path, speed, out_queue):
        super().__init__(daemon=True)
        self.path, self.speed, self.queue = path, speed, out_queue
        self.parser = P.Parser()
        self.status = f"replay {path}"
        self.want_connected = True
        self.running = True

    def send(self, command):
        return False

    def run(self):
        self.queue.put(("connected", None, 0))
        for record in paced(read_records(self.path), self.speed):
            if not self.running:
                return
            self.queue.put(("record", record, P.record_size(record)))
        self.status = f"replay finished: {self.path}"


def fmt(value, decimals=1, unit=""):
    if value is None or value == "":
        return "-"
    return f"{value:.{decimals}f}{unit}" if isinstance(value, float) else f"{value}{unit}"


def fmt_ms(microseconds):
    return fmt(microseconds / 1000 if microseconds else None)


def frame_image(latest):
    """The latest JPEG decoded, or a placeholder when the mode sends no JPEG or it was not recorded."""
    image = None
    if latest and latest["jpeg"]:
        image = cv2.imdecode(np.frombuffer(latest["jpeg"], np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        width, height = (latest["w"], latest["h"]) if latest else (320, 240)
        image = np.full((height, width, 3), 40, np.uint8)
        message = "no JPEG in this mode / not recorded" if latest else "waiting for frames"
        cv2.putText(image, message, (8, height // 2), FONT, 0.4, (200, 200, 200), 1)
    return image


def apply_overlay(view, pipeline, overlay_name):
    """Draw the selected heatmap; return (view, legend text with units)."""
    grid, movement = pipeline.grid, pipeline.movement
    if overlay_name == "presence" and grid and grid["frames"]:
        view, vmax = overlay(view, normalize_presence(grid, "frame"))
        return view, (f"PRESENCE INTENSITY (sampled, device): detections per processed frame per cell, "
                      f"max {vmax:.3g}, over {grid['frames']} frames")
    if overlay_name == "presence":
        return view, "PRESENCE INTENSITY (sampled): no grid yet (press g)"
    if overlay_name == "movement" and movement:
        view, vmax = overlay(view, movement.per_minute())
        return view, (f"APPROXIMATE IMAGE-SPACE MOVEMENT (PC tracklets): step crossings per minute per cell, "
                      f"max {vmax:.3g}, over {movement.observed_s:.0f} s")
    return view, "overlay: none"


def draw_detections(view, latest, scale):
    """Boxes with scores, the bottom-center point each heatmap uses, and the frame_id."""
    for box in latest["boxes"]:
        top_left = (int(box["x1"] * scale), int(box["y1"] * scale))
        bottom_right = (int(box["x2"] * scale), int(box["y2"] * scale))
        cv2.rectangle(view, top_left, bottom_right, GREEN, 2)
        cv2.putText(view, f"{box['score']:.2f}", (top_left[0], max(top_left[1] - 4, 12)), FONT, 0.5, GREEN, 1)
        cv2.circle(view, (int((box["x1"] + box["x2"]) / 2 * scale), bottom_right[1]), 3, YELLOW, -1)
    label = f"frame_id {latest['frame_id']}"
    cv2.putText(view, label, (8, 22), FONT, 0.6, (0, 0, 0), 3)
    cv2.putText(view, label, (8, 22), FONT, 0.6, WHITE, 1)


def compose_canvas(view, legend):
    """Image on the left, legend and limitations under it, room for the panel on the right."""
    legend_lines = textwrap.wrap(legend, FOOTER_WRAP_CHARS)
    footer = legend_lines + textwrap.wrap("Limits: " + LIMITATIONS, FOOTER_WRAP_CHARS)
    canvas_height = max(view.shape[0] + 16 * len(footer) + 8, MIN_CANVAS_HEIGHT)
    canvas = np.zeros((canvas_height, VIEW_WIDTH + PANEL_WIDTH, 3), np.uint8)
    canvas[:view.shape[0], :VIEW_WIDTH] = view
    for i, line in enumerate(footer):
        color = CYAN if i < len(legend_lines) else GREY
        cv2.putText(canvas, line, (6, view.shape[0] + 16 + 16 * i), FONT, 0.38, color, 1)
    return canvas


def panel_lines(pipeline, source, ui):
    """(text, color) lines for the right-hand panel; color None means the default grey."""
    hello = pipeline.hello or {}
    cfg = hello.get("config", {})
    metrics = pipeline.metrics or {}
    stats = ui["stats"]
    capture_to_display = ui.get("capture_to_display")
    clock_uncertainty = stats.get("clock_uncertainty_us")
    lines = [
        ("GMT people-flow viewer", WHITE),
        (source.status, YELLOW),
        ("", None),
        (f"device {hello.get('device_id', '-')}  fw {hello.get('fw_version', '-')}", None),
        (f"sensor {hello.get('sensor_pid', '-')}  idf {hello.get('idf', '-')}", None),
        (f"mode {cfg.get('mode', '-')}  {cfg.get('frame_size', '-')} q{cfg.get('jpeg_quality', '-')}"
         f"  every {cfg.get('infer_every_n', '-')}", None),
        ("", None),
        ("DEVICE (last METRICS window)", WHITE),
        (f"processed {fmt(metrics.get('fps_processed'))} fps   sent {fmt(metrics.get('fps_sent'))} fps", None),
        (f"t_infer mean {fmt_ms(metrics.get('stages.t_infer.mean'))} ms"
         f"   t_proc p95 {fmt_ms(metrics.get('stages.t_proc.p95'))} ms", None),
        (f"t_send mean {fmt_ms(metrics.get('stages.t_send.mean'))} ms"
         f"   tx_dropped {fmt(metrics.get('counters.tx_dropped'))}", None),
        (f"temp {fmt(metrics.get('temp_c'))} C   rssi {fmt(metrics.get('rssi'))}"
         f"   int_min {fmt(metrics.get('mem.int_min'))} B", None),
        ("", None),
        ("HOST", WHITE),
        (f"rx {fmt(stats.get('rx_pairs_per_s'))} frames/s   {fmt(stats.get('rx_bytes_per_s', 0) / 1000)} kB/s", None),
        (f"display {fmt(ui.get('display_fps'))} fps   draw {fmt(ui.get('display_ms'))} ms", None),
        (f"seq gaps {pipeline.stats['seq_gaps']}  crc {source.parser.crc_errors}  missing pairs "
         f"{pipeline.stats['missing_pairs']}", None),
        (f"frame_id gaps (device drops) {pipeline.stats['frame_id_gaps']}", None),
        (f"clock sync +-{clock_uncertainty / 1000:.1f} ms (min-RTT/2, raw offset in host_stats.csv)"
         if clock_uncertainty not in (None, "") else "clock sync: no PONG yet", None),
        (f"capture->display ~{capture_to_display[0]:.0f} ms +-{capture_to_display[1]:.0f} ms"
         if capture_to_display else "capture->display: n/a (needs PONG)", None),
        ("", None),
        ("EVENTS", WHITE),
    ]
    lines += [(event[:EVENT_LINE_CHARS], EVENT_COLOR) for event in list(pipeline.events)[-5:]]
    lines += [("", None),
              (f"overlay [o] {ui['overlay']}   recording [s] {'ON' if ui['recording'] else 'off'}", WHITE),
              ("[r] reset grids [g] get grid [m] mode [c] connect [q] quit", GREY)]
    if ui.get("note"):
        lines.append((ui["note"], NOTE_COLOR))
    return lines


def render(pipeline, source, ui):
    """Draw the whole window; also stores the drawing time in ui["display_ms"]."""
    started = time.perf_counter()
    latest = pipeline.latest
    image = frame_image(latest)
    scale = VIEW_WIDTH / image.shape[1]
    view = cv2.resize(image, (VIEW_WIDTH, int(image.shape[0] * scale)), interpolation=cv2.INTER_LINEAR)
    view, legend = apply_overlay(view, pipeline, ui["overlay"])
    if latest:
        draw_detections(view, latest, scale)
    if ui["recording"]:
        cv2.circle(view, (VIEW_WIDTH - 16, 16), 7, RED, -1)
    canvas = compose_canvas(view, legend)
    for i, (text, color) in enumerate(panel_lines(pipeline, source, ui)):
        cv2.putText(canvas, text, (VIEW_WIDTH + 10, 22 + 19 * i), FONT, 0.45, color or LIGHT_GREY, 1)
    ui["display_ms"] = (time.perf_counter() - started) * 1000
    return canvas


class Viewer:
    """Main loop: drain the source queue, ping, redraw on new frames, log host stats, handle keys."""

    def __init__(self, args):
        self.args = args
        self.queue = queue.Queue()
        self.source = (LiveSource(args.host, args.port, self.queue) if args.host
                       else ReplaySource(args.replay, args.speed, self.queue))
        self.logger = None if args.no_log else RunLogger(recording=args.record)
        self.pipeline = Pipeline(logger=self.logger)
        self.ui = {"overlay": args.overlay, "recording": args.record, "stats": {}, "note": ""}
        self.headless = bool(args.snapshot)
        self.canvas = None
        self.frames_drawn = 0
        self.last_drawn_frame_id = None
        self.fps_window_start = time.monotonic()
        self.key_actions = {ord("o"): self.cycle_overlay, ord("r"): self.reset_grids, ord("g"): self.request_grid,
                            ord("m"): self.next_mode, ord("s"): self.toggle_recording,
                            ord("c"): self.toggle_connection}

    def run(self):
        if not self.headless:
            cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_AUTOSIZE)
        self.source.start()
        started = next_stats = next_ping = time.monotonic()
        try:
            while True:
                self.drain_queue()
                now = time.monotonic()
                if self.args.host and now >= next_ping:
                    self.source.send(self.pipeline.clock.ping_payload())
                    next_ping = now + PING_INTERVAL_S
                self.redraw_if_needed(now >= next_stats)
                if now >= next_stats:
                    self.log_host_stats(now)
                    next_stats = now + STATS_INTERVAL_S
                if self.headless:
                    if now - started >= self.args.snapshot_after:
                        self.save_snapshot()
                        break
                    time.sleep(0.01)
                elif not self.show_and_handle_key():
                    break
        finally:
            self.source.running = False
            if self.logger:
                self.logger.close()
            if not self.headless:
                cv2.destroyAllWindows()

    def drain_queue(self):
        try:
            while True:
                kind, record, nbytes = self.queue.get_nowait()
                if kind == "connected":
                    self.pipeline.new_connection()
                elif kind == "bytes":
                    self.pipeline.stats["bytes"] += nbytes
                else:
                    self.pipeline.feed(record, nbytes)
        except queue.Empty:
            pass

    def redraw_if_needed(self, stats_due):
        latest = self.pipeline.latest
        new_frame = latest is not None and latest["frame_id"] != self.last_drawn_frame_id
        if self.canvas is not None and not new_frame and not stats_due:
            return
        self.canvas = render(self.pipeline, self.source, self.ui)
        if new_frame:
            self.last_drawn_frame_id = latest["frame_id"]
            self.frames_drawn += 1
            self.update_capture_to_display(latest)

    def update_capture_to_display(self, latest):
        """Only for live JPEG frames: replayed or metadata frames have no meaningful display time."""
        if latest["t_capture_us"] is None or not self.args.host:
            return
        estimate = self.pipeline.clock.capture_to_host_us(latest["t_capture_us"], host_us())
        self.ui["capture_to_display"] = (estimate[0] / 1000, estimate[1] / 1000) if estimate else None

    def log_host_stats(self, now):
        self.ui["display_fps"] = self.frames_drawn / max(now - self.fps_window_start, 1e-3)
        self.frames_drawn, self.fps_window_start = 0, now
        capture_to_display = self.ui.get("capture_to_display")
        self.ui["stats"] = self.pipeline.host_stats(self.source.parser, {
            "display_fps": round(self.ui["display_fps"], 2), "display_ms": round(self.ui.get("display_ms", 0), 2),
            "capture_to_display_ms": round(capture_to_display[0], 1) if capture_to_display else "",
            "capture_to_display_unc_ms": round(capture_to_display[1], 1) if capture_to_display else ""})

    def save_snapshot(self):
        cv2.imwrite(self.args.snapshot, self.canvas)
        log_dir = self.logger.dir if self.logger else None
        print(f"snapshot -> {self.args.snapshot}; stats {self.pipeline.stats}; log {log_dir}")

    def show_and_handle_key(self):
        """Returns False when the user quits or closes the window."""
        cv2.imshow(WINDOW_TITLE, self.canvas)
        key = cv2.waitKey(15) & 0xFF
        if key in (ord("q"), KEY_ESCAPE) or cv2.getWindowProperty(WINDOW_TITLE, cv2.WND_PROP_VISIBLE) < 1:
            return False
        action = self.key_actions.get(key)
        if action:
            action()
        return True

    def cycle_overlay(self):
        self.ui["overlay"] = OVERLAYS[(OVERLAYS.index(self.ui["overlay"]) + 1) % len(OVERLAYS)]

    def reset_grids(self):
        self.pipeline.reset_movement()
        self.pipeline.grid = None
        sent = self.source.send({"cmd": "reset_grid"})
        self.ui["note"] = "grids reset" if sent else "PC grid reset (no device)"

    def request_grid(self):
        self.ui["note"] = "grid requested" if self.source.send({"cmd": "get_grid"}) else "no device connected"

    def next_mode(self):
        current = self.pipeline.mode or MODES[-1]
        following = MODES[(MODES.index(current) + 1) % len(MODES)] if current in MODES else MODES[0]
        sent = self.source.send({"cmd": "set", "values": {"mode": following}})
        self.ui["note"] = f"mode -> {following}" if sent else "no device connected"

    def toggle_recording(self):
        self.ui["recording"] = not self.ui["recording"]
        if self.logger:
            self.logger.recording = self.ui["recording"]
        self.ui["note"] = "recording ON: JPEGs saved to the run folder" if self.ui["recording"] else "recording off"

    def toggle_connection(self):
        self.source.want_connected = not self.source.want_connected


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--host")
    source.add_argument("--replay", help="run directory or .gmtp file")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed (0 = as fast as possible)")
    parser.add_argument("--record", action="store_true", help="start with JPEG recording on")
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument("--snapshot", help="headless: save the view to this PNG and exit")
    parser.add_argument("--snapshot-after", type=float, default=10.0)
    parser.add_argument("--overlay", choices=OVERLAYS, default="none")
    return parser.parse_args()


def main():
    Viewer(parse_args()).run()
    return 0

