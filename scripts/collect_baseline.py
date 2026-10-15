"""Gate 4 baseline: every mode x N repeats, fixed duration after warm-up, with full provenance.

  python scripts/collect_baseline.py --host 192.168.1.248 --room-temp-c 26.5 --scene static_silhouettes \
      --heatsink yes --repeats 3 --seconds 60

Before the first trial the board streams in full_stream for --initial-warmup-s (default 120 s, handoff
section 9) so the die temperature settles. Each trial: set mode -> wait --warmup-s -> log --seconds of
records into its own run folder. Writes baselines/<date>_<device>_<scene>/:
  baseline.json  provenance: board MAC, sensor, fw hash, versions, config, room temp, heatsink, scene, date
  summary.csv    one row per trial (fps, stage means, mean of per-window p95, drops, temperature, memory)
  <mode>_r<k>/   the normal run log of that trial (see pc/logger.py)
Nothing is tuned or compared here; the numbers are for students to explain.
"""
import argparse
import csv
import json
import socket
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pc"))
import protocol as P  # noqa: E402
from logger import RunLogger  # noqa: E402
from pipeline import Pipeline  # noqa: E402

MODES = ["full_stream", "metadata_stream", "detect_no_stream", "camera_only", "inference_only"]
SUMMARY = ["mode", "repeat", "seconds", "windows", "fps_processed_mean", "fps_sent_mean", "capture_fps",
           "t_wait_frame_mean_us", "t_decode_mean_us", "t_pre_mean_us", "t_infer_mean_us", "t_post_mean_us",
           "t_proc_mean_us", "t_proc_p95_mean_us", "t_send_mean_us", "tx_dropped", "rx_pairs", "rx_det_only",
           "seq_gaps", "crc_errors", "missing_pairs", "frame_id_gaps", "temp_start_c", "temp_end_c", "temp_max_c",
           "int_min_end", "ps_min_end", "rssi_mean", "run_dir"]


class Conn:
    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=5)
        self.s.settimeout(0.5)
        self.parser = P.Parser()

    def send(self, obj):
        self.s.sendall(P.encode_command(obj))

    def records(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                data = self.s.recv(65536)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("device closed the connection")
            yield len(data), self.parser.feed(data)

    def close(self):
        self.s.close()


def wait_for_mode(conn, pipe, mode, timeout=20):
    for _, recs in conn.records(timeout):
        for r in recs:
            pipe.feed(r)
        if pipe.mode == mode:
            return
    raise TimeoutError(f"device did not confirm mode {mode}")


def wmean(rows, stage):
    """Sample-weighted mean of per-window means."""
    pairs = [(r.get(f"stages.{stage}.mean"), r.get(f"stages.{stage}.n")) for r in rows]
    pairs = [(m, n) for m, n in pairs if m is not None and n]
    return round(sum(m * n for m, n in pairs) / sum(n for _, n in pairs)) if pairs else ""


def mean(rows, key, nd=2):
    v = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.fmean(v), nd) if v else ""


def trial(a, out, mode, k):
    conn = Conn(a.host, a.port)
    pipe = Pipeline()
    conn.send({"cmd": "set", "values": {"mode": mode, "infer_only_runs": 65535}})
    wait_for_mode(conn, pipe, mode)
    for _, recs in conn.records(a.warmup_s):  # warm-up after the mode switch: not logged
        for r in recs:
            pipe.feed(r)

    logger = RunLogger(root=out / f"{mode}_r{k}")
    logger.record(P.Record(P.HELLO, 0, 0, 0, json.dumps(pipe.hello).encode()))  # names the run folder
    pipe = Pipeline(logger=logger)
    pipe.hello = json.loads(json.dumps(logger.hellos[0]))
    metrics = []
    t0 = time.time()
    for n, recs in conn.records(a.seconds):
        pipe.stats["bytes"] += n
        for r in recs:
            pipe.feed(r)
            if r.type == P.METRICS:
                metrics.append(P.flatten(P.decode_json(r.payload)))
        if time.time() - t0 >= 1:
            pipe.host_stats(conn.parser)
            t0 = time.time()
    conn.close()
    logger.close()

    first, last = (metrics[0], metrics[-1]) if metrics else ({}, {})
    span = (last.get("uptime_s", 0) - first.get("uptime_s", 0)) or 1
    temps = [round(m["temp_c"], 1) for m in metrics if m.get("temp_c") is not None]
    return {
        "mode": mode, "repeat": k, "seconds": a.seconds, "windows": len(metrics),
        "fps_processed_mean": mean(metrics, "fps_processed"), "fps_sent_mean": mean(metrics, "fps_sent"),
        "capture_fps": round((last.get("counters.cap_ok", 0) - first.get("counters.cap_ok", 0)) / span, 2),
        **{f"{s}_mean_us": wmean(metrics, s) for s in ("t_wait_frame", "t_decode", "t_pre", "t_infer", "t_post",
                                                       "t_proc", "t_send")},
        "t_proc_p95_mean_us": mean(metrics, "stages.t_proc.p95", 0),
        "tx_dropped": last.get("counters.tx_dropped", 0) - first.get("counters.tx_dropped", 0),
        "rx_pairs": pipe.stats["pairs"], "rx_det_only": pipe.stats["det_only"], "seq_gaps": pipe.stats["seq_gaps"],
        "crc_errors": conn.parser.crc_errors, "missing_pairs": pipe.stats["missing_pairs"],
        "frame_id_gaps": pipe.stats["frame_id_gaps"],
        "temp_start_c": temps[0] if temps else "", "temp_end_c": temps[-1] if temps else "",
        "temp_max_c": max(temps) if temps else "",
        "int_min_end": last.get("mem.int_min", ""), "ps_min_end": last.get("mem.ps_min", ""),
        "rssi_mean": mean(metrics, "rssi", 1), "run_dir": str(logger.dir.relative_to(out)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--room-temp-c", type=float, required=True, help="measured room temperature")
    ap.add_argument("--scene", required=True, help="e.g. static_silhouettes, walking_video, live_volunteers")
    ap.add_argument("--heatsink", choices=["yes", "no"], required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--warmup-s", type=float, default=15, help="after each mode switch")
    ap.add_argument("--initial-warmup-s", type=float, default=120, help="full_stream before the first trial")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--out", type=Path, default=ROOT / "baselines")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    modes = a.modes.split(",")

    conn = Conn(a.host, a.port)
    pipe = Pipeline()
    conn.send({"cmd": "set", "values": {"mode": "full_stream"}})
    print(f"initial warm-up: {a.initial_warmup_s:.0f} s of full_stream", flush=True)
    for _, recs in conn.records(a.initial_warmup_s):
        for r in recs:
            pipe.feed(r)
    conn.close()
    hello = pipe.hello or {}
    if not hello:
        sys.exit("no HELLO from the device")
    out = a.out / f"{datetime.now():%Y%m%d_%H%M}_{hello.get('device_id')}_{a.scene}"
    out.mkdir(parents=True)
    provenance = {
        "date": datetime.now().isoformat(timespec="seconds"), "scene": a.scene, "room_temp_c": a.room_temp_c,
        "heatsink": a.heatsink, "repeats": a.repeats, "seconds": a.seconds, "warmup_s": a.warmup_s,
        "initial_warmup_s": a.initial_warmup_s, "modes": modes, "note": a.note,
        "device": {k: hello.get(k) for k in ("device_id", "mac", "fw_version", "fw_build", "idf", "esp_dl",
                                             "esp32_camera", "pedestrian_detect", "model", "chip_rev", "sensor_pid",
                                             "psram_bytes", "flash_bytes", "reset_reason")},
        "config": hello.get("config"),
    }
    (out / "baseline.json").write_text(json.dumps(provenance, indent=1))
    if str(hello.get("fw_version", "")).endswith("-dirty"):
        print("WARNING: firmware was built from uncommitted changes (-dirty): not a valid baseline", flush=True)

    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, SUMMARY)
        w.writeheader()
        for k in range(1, a.repeats + 1):
            for mode in modes:
                print(f"trial {mode} r{k}: {a.warmup_s:.0f} s warm-up + {a.seconds:.0f} s", flush=True)
                row = trial(a, out, mode, k)
                w.writerow(row)
                f.flush()
                print(f"  fps processed {row['fps_processed_mean']} sent {row['fps_sent_mean']} "
                      f"capture {row['capture_fps']}  t_proc {row['t_proc_mean_us']} us  temp {row['temp_end_c']} C",
                      flush=True)
    restore = Conn(a.host, a.port)
    restore.send({"cmd": "set", "values": {"mode": "full_stream", "infer_only_runs": 100}})
    restore.close()
    print(f"done: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
