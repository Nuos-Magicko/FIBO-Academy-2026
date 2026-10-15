"""PING/PONG clock-offset estimate between host and device clocks.

offset = t_dev - (t_host_send + rtt/2), uncertainty = +-rtt/2 (the true offset lies in that band if
the device stamped the reply somewhere inside the round trip). The sample with the smallest RTT in
the recent window gives the tightest bound. Any end-to-end latency built on this must carry the
uncertainty; without PONGs there is no end-to-end claim at all.
"""
import time
from collections import deque


def host_us():
    return time.perf_counter_ns() // 1000  # monotonic_ns ticks at ~15.6 ms on Windows


class ClockSync:
    def __init__(self, window=20):
        self.samples = deque(maxlen=window)  # (rtt_us, offset_us)
        self.pending = set()

    def ping_payload(self):
        t = host_us()
        self.pending.add(t)
        return {"cmd": "ping", "t_host_us": t}

    def on_pong(self, pong, t_recv_us=None):
        t_host = pong.get("t_host_us")
        if t_host not in self.pending:
            return None
        self.pending.discard(t_host)
        t_recv = host_us() if t_recv_us is None else t_recv_us
        rtt = t_recv - t_host
        self.samples.append((rtt, pong["t_dev_us"] - (t_host + rtt / 2)))
        return self.samples[-1]

    def best(self):
        """(offset_us, uncertainty_us) from the min-RTT sample, or None."""
        if not self.samples:
            return None
        rtt, off = min(self.samples)
        return off, rtt / 2

    def capture_to_host_us(self, t_capture_dev_us, t_host_now_us):
        """Latency from device capture to a host event, as (estimate_us, +-uncertainty_us) or None."""
        b = self.best()
        if b is None:
            return None
        off, unc = b
        return t_host_now_us + off - t_capture_dev_us, unc
