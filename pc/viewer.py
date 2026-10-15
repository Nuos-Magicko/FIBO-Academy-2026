"""GMT people-flow viewer and logger (OpenCV window).

  python pc/viewer.py --host 192.168.1.248            # live device
  python pc/viewer.py --replay samples/synthetic_run  # recorded or synthetic run, same pipeline
  python pc/viewer.py --host IP --snapshot out.png --snapshot-after 10   # headless: save one view, exit

Keys: o overlay (none / presence / movement)   r reset grids (device + PC)   g request grid
      m next mode   s recording on/off (JPEGs to disk)   c connect/disconnect   q or Esc quit

Telemetry logs (runs/<time>_<device>/) are always written unless --no-log. Camera JPEGs are saved
only while recording is on (off by default): the camera can see people.
"""
import argparse
import queue
import socket
import sys
import textwrap
import threading
import time

import cv2
import numpy as np

import protocol as P
from clock_sync import host_us
from heatmap import LIMITATIONS, normalize_presence, overlay
from logger import RunLogger
from pipeline import Pipeline
from replay import paced, read_records

MODES = ["full_stream", "metadata_stream", "detect_no_stream", "camera_only", "inference_only"]
OVERLAYS = ["none", "presence", "movement"]
PANEL_W = 420
VIEW_W = 640


class LiveSource(threading.Thread):
    """Socket reader thread: bytes -> Parser -> records queue. Connect/disconnect on request."""

    def __init__(self, host, port, out_q):
        super().__init__(daemon=True)
        self.host, self.port, self.q = host, port, out_q
        self.parser = P.Parser()
        self.want_connected = True
        self.sock = None
        self.status = "connecting"
        self.running = True

    def send(self, obj):
        s = self.sock
        if s is None:
            return False
        try:
            s.sendall(P.encode_command(obj))
            return True
        except OSError:
            return False

    def run(self):
        while self.running:
            if not self.want_connected:
                if self.sock:
                    self.sock.close()
                    self.sock = None
                self.status = "disconnected (press c)"
                time.sleep(0.1)
                continue
            if self.sock is None:
                try:
                    self.sock = socket.create_connection((self.host, self.port), timeout=3)
                    self.sock.settimeout(0.5)
                    self.parser.buf.clear()
                    self.q.put(("connected", None, 0))
                    self.status = f"connected {self.host}:{self.port}"
                except OSError as e:
                    self.status = f"connect failed: {e.__class__.__name__}, retrying"
                    time.sleep(2)
                    continue
            try:
                data = self.sock.recv(65536)
                if not data:
                    raise ConnectionError("closed by device")
            except socket.timeout:
                continue
            except OSError as e:
                self.status = f"connection lost: {e.__class__.__name__}, retrying"
                self.sock.close()
                self.sock = None
                continue
            self.q.put(("bytes", None, len(data)))
            for r in self.parser.feed(data):
                self.q.put(("record", r, 0))


class ReplaySource(threading.Thread):
    def __init__(self, path, speed, out_q):
        super().__init__(daemon=True)
        self.path, self.speed, self.q = path, speed, out_q
        self.parser = P.Parser()  # replay records are pre-parsed; kept for the stats interface
        self.status = f"replay {path}"
        self.want_connected = True
        self.running = True

    def send(self, obj):
        return False

    def run(self):
        self.q.put(("connected", None, 0))
        for r in paced(read_records(self.path), self.speed):
            if not self.running:
                return
            self.q.put(("record", r, P.HDR.size + len(r.payload) + 4))
        self.status = f"replay finished: {self.path}"


def fmt(v, nd=1, unit=""):
    if v is None or v == "":
        return "-"
    return f"{v:.{nd}f}{unit}" if isinstance(v, float) else f"{v}{unit}"


def render(pipe, src, ui):
    lt = pipe.latest
    w, h = (lt["w"], lt["h"]) if lt else (320, 240)
    t0 = time.perf_counter()
    img = None
    if lt and lt["jpeg"]:
        img = cv2.imdecode(np.frombuffer(lt["jpeg"], np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        img = np.full((h, w, 3), 40, np.uint8)
        msg = "no JPEG in this mode / not recorded" if lt else "waiting for frames"
        cv2.putText(img, msg, (8, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
    scale = VIEW_W / img.shape[1]
    view = cv2.resize(img, (VIEW_W, int(img.shape[0] * scale)), interpolation=cv2.INTER_LINEAR)

    legend = "overlay: none"
    if ui["overlay"] == "presence" and pipe.grid and pipe.grid["frames"]:
        view, vmax = overlay(view, normalize_presence(pipe.grid, "frame"))
        legend = (f"PRESENCE INTENSITY (sampled, device): detections per processed frame per cell, "
                  f"max {vmax:.3g}, over {pipe.grid['frames']} frames")
    elif ui["overlay"] == "presence":
        legend = "PRESENCE INTENSITY (sampled): no grid yet (press g)"
    elif ui["overlay"] == "movement" and pipe.movement:
        view, vmax = overlay(view, pipe.movement.per_minute())
        legend = (f"APPROXIMATE IMAGE-SPACE MOVEMENT (PC tracklets): step crossings per minute per cell, "
                  f"max {vmax:.3g}, over {pipe.movement.observed_s:.0f} s")

    if lt:
        for b in lt["boxes"]:
            p1 = (int(b["x1"] * scale), int(b["y1"] * scale))
            p2 = (int(b["x2"] * scale), int(b["y2"] * scale))
            cv2.rectangle(view, p1, p2, (0, 255, 0), 2)
            cv2.putText(view, f"{b['score']:.2f}", (p1[0], max(p1[1] - 4, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 255, 0), 1)
            cv2.circle(view, (int((b["x1"] + b["x2"]) / 2 * scale), p2[1]), 3, (0, 255, 255), -1)
        cv2.putText(view, f"frame_id {lt['frame_id']}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
        cv2.putText(view, f"frame_id {lt['frame_id']}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    if ui["recording"]:
        cv2.circle(view, (VIEW_W - 16, 16), 7, (0, 0, 255), -1)

    # legend + limitations under the image
    foot = textwrap.wrap(legend, 92) + textwrap.wrap("Limits: " + LIMITATIONS, 92)
    foot_h = 16 * len(foot) + 8
    canvas_h = max(view.shape[0] + foot_h, 640)
    canvas = np.zeros((canvas_h, VIEW_W + PANEL_W, 3), np.uint8)
    canvas[:view.shape[0], :VIEW_W] = view
    for i, line in enumerate(foot):
        cv2.putText(canvas, line, (6, view.shape[0] + 16 + 16 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (255, 255, 160) if i < len(textwrap.wrap(legend, 92)) else (170, 170, 170), 1)

    hello = pipe.hello or {}
    cfg = hello.get("config", {})
    m = pipe.metrics or {}
    st = ui["stats"]
    c2d = ui.get("c2d")
    lines = [
        ("GMT people-flow viewer", (255, 255, 255)),
        (src.status, (0, 255, 255)),
        ("", None),
        (f"device {hello.get('device_id', '-')}  fw {hello.get('fw_version', '-')}", None),
        (f"sensor {hello.get('sensor_pid', '-')}  idf {hello.get('idf', '-')}", None),
        (f"mode {cfg.get('mode', '-')}  {cfg.get('frame_size', '-')} q{cfg.get('jpeg_quality', '-')}"
         f"  every {cfg.get('infer_every_n', '-')}", None),
        ("", None),
        ("DEVICE (last METRICS window)", (255, 255, 255)),
        (f"processed {fmt(m.get('fps_processed'))} fps   sent {fmt(m.get('fps_sent'))} fps", None),
        (f"t_infer mean {fmt(m.get('stages.t_infer.mean', 0) / 1000 if m.get('stages.t_infer.mean') else None)} ms"
         f"   t_proc p95 {fmt(m.get('stages.t_proc.p95', 0) / 1000 if m.get('stages.t_proc.p95') else None)} ms", None),
        (f"t_send mean {fmt(m.get('stages.t_send.mean', 0) / 1000 if m.get('stages.t_send.mean') else None)} ms"
         f"   tx_dropped {fmt(m.get('counters.tx_dropped'))}", None),
        (f"temp {fmt(m.get('temp_c'))} C   rssi {fmt(m.get('rssi'))}   int_min {fmt(m.get('mem.int_min'))} B", None),
        ("", None),
        ("HOST", (255, 255, 255)),
        (f"rx {fmt(st.get('rx_pairs_per_s'))} frames/s   {fmt(st.get('rx_bytes_per_s', 0) / 1000)} kB/s", None),
        (f"display {fmt(ui.get('display_fps'))} fps   draw {fmt(ui.get('display_ms'))} ms", None),
        (f"seq gaps {pipe.stats['seq_gaps']}  crc {src.parser.crc_errors}  missing pairs "
         f"{pipe.stats['missing_pairs']}", None),
        (f"frame_id gaps (device drops) {pipe.stats['frame_id_gaps']}", None),
        (f"clock sync +-{st['clock_uncertainty_us'] / 1000:.1f} ms (min-RTT/2, raw offset in host_stats.csv)"
         if st.get("clock_uncertainty_us") not in (None, "") else "clock sync: no PONG yet", None),
        (f"capture->display ~{c2d[0]:.0f} ms +-{c2d[1]:.0f} ms" if c2d else "capture->display: n/a (needs PONG)",
         None),
        ("", None),
        ("EVENTS", (255, 255, 255)),
    ] + [(e[:50], (180, 180, 255)) for e in list(pipe.events)[-5:]] + [
        ("", None),
        (f"overlay [o] {ui['overlay']}   recording [s] {'ON' if ui['recording'] else 'off'}", (255, 255, 255)),
        ("[r] reset grids [g] get grid [m] mode [c] connect [q] quit", (170, 170, 170)),
    ]
    if ui.get("note"):
        lines.append((ui["note"], (0, 200, 255)))
    for i, (text, color) in enumerate(lines):
        cv2.putText(canvas, text, (VIEW_W + 10, 22 + 19 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    color or (210, 210, 210), 1)
    ui["display_ms"] = (time.perf_counter() - t0) * 1000
    return canvas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src_g = ap.add_mutually_exclusive_group(required=True)
    src_g.add_argument("--host")
    src_g.add_argument("--replay", help="run directory or .gmtp file")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed (0 = as fast as possible)")
    ap.add_argument("--record", action="store_true", help="start with JPEG recording on")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--snapshot", help="headless: save the view to this PNG and exit")
    ap.add_argument("--snapshot-after", type=float, default=10.0)
    ap.add_argument("--overlay", choices=OVERLAYS, default="none")
    a = ap.parse_args()

    q = queue.Queue()
    src = LiveSource(a.host, a.port, q) if a.host else ReplaySource(a.replay, a.speed, q)
    logger = None if a.no_log else RunLogger(recording=a.record)
    pipe = Pipeline(logger=logger)
    ui = {"overlay": a.overlay, "recording": a.record, "stats": {}, "note": ""}
    headless = bool(a.snapshot)
    if not headless:
        cv2.namedWindow("GMT people-flow", cv2.WINDOW_AUTOSIZE)
    src.start()

    t_start = time.monotonic()
    next_stats = next_ping = time.monotonic()
    frames_drawn, last_drawn_fid, fps_t0 = 0, None, time.monotonic()
    canvas = None
    try:
        while True:
            try:
                while True:
                    kind, r, nbytes = q.get_nowait()
                    if kind == "connected":
                        pipe.new_connection()
                    elif kind == "bytes":
                        pipe.stats["bytes"] += nbytes
                    else:
                        pipe.feed(r, nbytes)
            except queue.Empty:
                pass
            now = time.monotonic()
            if a.host and now >= next_ping:
                src.send(pipe.clock.ping_payload())
                next_ping = now + 2
            lt = pipe.latest
            if canvas is None or (lt and lt["frame_id"] != last_drawn_fid) or now >= next_stats:
                canvas = render(pipe, src, ui)
                if lt and lt["frame_id"] != last_drawn_fid:
                    last_drawn_fid = lt["frame_id"]
                    frames_drawn += 1
                    if lt["t_capture_us"] is not None and a.host:
                        c = pipe.clock.capture_to_host_us(lt["t_capture_us"], host_us())
                        ui["c2d"] = (c[0] / 1000, c[1] / 1000) if c else None
            if now >= next_stats:
                ui["display_fps"] = frames_drawn / max(now - fps_t0, 1e-3)
                frames_drawn, fps_t0 = 0, now
                c2d = ui.get("c2d")
                ui["stats"] = pipe.host_stats(src.parser, {
                    "display_fps": round(ui["display_fps"], 2), "display_ms": round(ui.get("display_ms", 0), 2),
                    "capture_to_display_ms": round(c2d[0], 1) if c2d else "",
                    "capture_to_display_unc_ms": round(c2d[1], 1) if c2d else ""})
                next_stats = now + 1
            if headless:
                if now - t_start >= a.snapshot_after:
                    cv2.imwrite(a.snapshot, canvas)
                    print(f"snapshot -> {a.snapshot}; stats {pipe.stats}; log {logger.dir if logger else None}")
                    break
                time.sleep(0.01)
                continue
            cv2.imshow("GMT people-flow", canvas)
            key = cv2.waitKey(15) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty("GMT people-flow", cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord("o"):
                ui["overlay"] = OVERLAYS[(OVERLAYS.index(ui["overlay"]) + 1) % len(OVERLAYS)]
            elif key == ord("r"):
                pipe.reset_movement()
                ui["note"] = "grids reset" if src.send({"cmd": "reset_grid"}) else "PC grid reset (no device)"
                pipe.grid = None
            elif key == ord("g"):
                ui["note"] = "grid requested" if src.send({"cmd": "get_grid"}) else "no device connected"
            elif key == ord("m"):
                cur = (pipe.hello or {}).get("config", {}).get("mode", MODES[-1])
                nxt = MODES[(MODES.index(cur) + 1) % len(MODES)] if cur in MODES else MODES[0]
                ok = src.send({"cmd": "set", "values": {"mode": nxt}})
                ui["note"] = f"mode -> {nxt}" if ok else "no device connected"
            elif key == ord("s"):
                ui["recording"] = not ui["recording"]
                if logger:
                    logger.recording = ui["recording"]
                ui["note"] = "recording ON: JPEGs saved to the run folder" if ui["recording"] else "recording off"
            elif key == ord("c"):
                src.want_connected = not src.want_connected
    finally:
        src.running = False
        if logger:
            logger.close()
        if not headless:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
