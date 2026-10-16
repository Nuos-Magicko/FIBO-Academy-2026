"""Comparing the board's clock with the laptop's clock.

The two clocks show different times, like two watches that were never set to match. To compare
them, the laptop sends a "ping" with its own time; the board answers at once with its time
(a "pong"). The round trip time (RTT) is how long that took.

The board wrote its time somewhere during the round trip, probably near the middle, so:
    offset      = board_time - (laptop_time_sent + RTT / 2)
    uncertainty = +- RTT / 2
The fastest round trip gives the smallest uncertainty, so that one is used.

Any camera-to-screen time built on this must be reported with its +- uncertainty. Without a
pong there is no camera-to-screen time at all.
"""
import time
from collections import deque


def host_us():
    """Host clock in microseconds. perf_counter is used because monotonic ticks at ~15.6 ms on Windows."""
    return time.perf_counter_ns() // 1000


class ClockSync:
    """Keeps the last `window` (rtt_us, offset_us) samples and answers from the fastest round trip."""

    def __init__(self, window=20):
        self.samples = deque(maxlen=window)
        self.pending = set()

    def ping_payload(self):
        """A ping command stamped with the current host time."""
        t_host = host_us()
        self.pending.add(t_host)
        return {"cmd": "ping", "t_host_us": t_host}

    def on_pong(self, pong, t_received_us=None):
        """Record a PONG; returns its (rtt_us, offset_us), or None for a reply to an unknown ping."""
        t_host = pong.get("t_host_us")
        if t_host not in self.pending:
            return None
        self.pending.discard(t_host)
        t_received = host_us() if t_received_us is None else t_received_us
        rtt = t_received - t_host
        self.samples.append((rtt, pong["t_dev_us"] - (t_host + rtt / 2)))
        return self.samples[-1]

    def best(self):
        """(offset_us, uncertainty_us) from the min-RTT sample, or None."""
        if not self.samples:
            return None
        rtt, offset = min(self.samples)
        return offset, rtt / 2

    def capture_to_host_us(self, t_capture_dev_us, t_host_now_us):
        """Latency from device capture to a host event, as (estimate_us, +-uncertainty_us) or None."""
        best = self.best()
        if best is None:
            return None
        offset, uncertainty = best
        return t_host_now_us + offset - t_capture_dev_us, uncertainty
