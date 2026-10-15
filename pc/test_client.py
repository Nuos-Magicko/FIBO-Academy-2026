"""Gate 2 test client: connect, send commands, check bundle pairing, report counters.

  python pc/test_client.py HOST --seconds 30
  python pc/test_client.py HOST --seconds 30 --slow-ms 200 --recv-bytes 2048   # slow reader (~10 KB/s)
  python pc/test_client.py HOST --cmd '{"cmd":"set","values":{"mode":"metadata_stream"}}'
  python pc/test_client.py HOST --reconnects 3                       # disconnect/reconnect cycles
  python pc/test_client.py HOST --save-frame frame.jpg               # one JPEG + its boxes (.json)

Prints one JSON summary. Exit code 1 if pairing or integrity checks fail.
"""
import argparse
import json
import socket
import sys
import time

import protocol as P


def now_us():
    return time.perf_counter_ns() // 1000  # monotonic_ns ticks at ~15.6 ms on Windows


def run_session(a, seconds, summary):
    s = socket.create_connection((a.host, a.port), timeout=5)
    s.settimeout(0.5)
    parser = P.Parser()
    last_seq = None
    last_jpeg_id = None
    pings = {}
    sent_cmds = False
    t_end = time.time() + seconds
    next_ping = time.time() + 1
    saved = False
    while time.time() < t_end:
        try:
            data = s.recv(a.recv_bytes)
            if not data:
                summary["server_closed"] += 1
                break
        except socket.timeout:
            data = b""
        if a.slow_ms:
            time.sleep(a.slow_ms / 1000)
        summary["bytes"] += len(data)
        for r in parser.feed(data):
            c = summary["counts"]
            c[r.name] = c.get(r.name, 0) + 1
            if last_seq is not None and r.seq != last_seq + 1:
                summary["seq_gaps"] += 1
            last_seq = r.seq
            if r.type == P.HELLO:
                summary["hello"] = P.decode_json(r.payload)
                summary.setdefault("first_hello_config", summary["hello"].get("config"))
                if not sent_cmds:
                    for cmd in a.cmd:
                        s.sendall(P.encode_command(json.loads(cmd)))
                    sent_cmds = True
            elif r.type == P.JPEG_FRAME:
                if last_jpeg_id is not None:
                    summary["unpaired_jpeg"] += 1  # previous JPEG never got its DETECTIONS
                last_jpeg_id = r.frame_id
                jf = P.decode_jpeg_frame(r.payload)
            elif r.type == P.DETECTIONS:
                d = P.decode_detections(r.payload)
                mode = (summary.get("hello") or {}).get("config", {}).get("mode")
                if last_jpeg_id is not None:
                    if last_jpeg_id == r.frame_id:
                        summary["pairs"] += 1
                        if a.save_frame and not saved:
                            open(a.save_frame, "wb").write(jf["jpeg"])
                            json.dump(d, open(a.save_frame + ".json", "w"), indent=1)
                            saved = True
                    else:
                        summary["pair_mismatch"] += 1  # DETECTIONS[n+1] after JPEG[n]: must never happen
                    last_jpeg_id = None
                elif mode == "full_stream":
                    summary["det_without_jpeg"] += 1
                else:
                    summary["det_only"] += 1
                if summary["last_frame_id"] is not None and r.frame_id > summary["last_frame_id"] + 1:
                    summary["frame_id_gaps"] += r.frame_id - summary["last_frame_id"] - 1
                summary["last_frame_id"] = r.frame_id
                summary["boxes"] += len(d["boxes"])
            elif r.type == P.METRICS:
                summary["last_metrics"] = P.flatten(P.decode_json(r.payload))
            elif r.type == P.EVENT:
                e = P.decode_json(r.payload)
                summary["events"].append(f'{e["severity"]}:{e["code"]}: {e["text"]}')
            elif r.type == P.PONG:
                t_recv = now_us()
                pong = P.decode_json(r.payload)
                t_host = pong["t_host_us"]
                if t_host in pings:
                    rtt = t_recv - t_host
                    summary["pings"].append({"rtt_us": rtt,
                                             "offset_us": pong["t_dev_us"] - (t_host + rtt / 2),
                                             "uncertainty_us": rtt / 2})
            elif r.type == P.PRESENCE_GRID:
                g = P.decode_grid(r.payload)
                summary["last_grid"] = {k: g[k] for k in ("cols", "rows", "frames", "elapsed_us")}
                summary["last_grid"]["total"] = sum(map(sum, g["counts"]))
        if a.ping and time.time() >= next_ping:
            t = now_us()
            pings[t] = True
            s.sendall(P.encode_command({"cmd": "ping", "t_host_us": t}))
            next_ping = time.time() + 1
    summary["crc_errors"] += parser.crc_errors
    summary["resyncs"] += parser.resyncs
    s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--slow-ms", type=float, default=0, help="sleep after every recv (test harness only)")
    ap.add_argument("--recv-bytes", type=int, default=65536, help="max bytes per recv (with --slow-ms: reader throughput cap)")
    ap.add_argument("--cmd", action="append", default=[], help="COMMAND JSON sent after HELLO")
    ap.add_argument("--ping", action="store_true", help="PING once per second; report clock offset")
    ap.add_argument("--reconnects", type=int, default=0, help="extra disconnect/reconnect cycles")
    ap.add_argument("--save-frame")
    a = ap.parse_args()

    summary = {"sessions": 0, "bytes": 0, "counts": {}, "seq_gaps": 0, "crc_errors": 0, "resyncs": 0,
               "pairs": 0, "pair_mismatch": 0, "unpaired_jpeg": 0, "det_without_jpeg": 0, "det_only": 0,
               "frame_id_gaps": 0, "last_frame_id": None, "boxes": 0, "server_closed": 0,
               "events": [], "pings": [], "hello": None, "last_metrics": None, "last_grid": None}
    t0 = time.time()
    for i in range(a.reconnects + 1):
        run_session(a, a.seconds / (a.reconnects + 1), summary)
        summary["sessions"] += 1
        summary["last_frame_id"] = None  # frame ids keep counting on the device; gaps only within a session
    elapsed = time.time() - t0
    summary["elapsed_s"] = round(elapsed, 1)
    summary["rx_fps"] = round(summary["counts"].get("DETECTIONS", 0) / elapsed, 2)
    if summary["pings"]:
        best = min(summary["pings"], key=lambda p: p["rtt_us"])
        summary["clock"] = {"n": len(summary["pings"]), "best_rtt_us": best["rtt_us"],
                            "offset_us": round(best["offset_us"]), "uncertainty_us": best["uncertainty_us"]}
        del summary["pings"]
    if summary["hello"]:
        h = summary["hello"]
        summary["hello"] = {k: h.get(k) for k in ("device_id", "fw_version", "sensor_pid", "ip", "config")}
    print(json.dumps(summary, indent=1))
    bad = summary["pair_mismatch"] or summary["crc_errors"] or summary["seq_gaps"] or summary["det_without_jpeg"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
