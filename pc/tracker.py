"""Ephemeral nearest-neighbour tracklets on box bottom-center points (handoff F3).

No IDs are persisted and no appearance features are used: a tracklet only links points that are
close in image space between consecutive processed frames. Tracklets expire after `max_missed`
frames without a match. A tracklet is NOT a person count.

Steps shorter than `min_step_px` are not accumulated (suppresses jitter and stationary people):
each tracklet keeps an anchor and only emits a step once it has moved `min_step_px` from it.
"""
import math
from dataclasses import dataclass


@dataclass
class Tracklet:
    x: float
    y: float
    ax: float  # anchor: where the last accumulated step ended
    ay: float
    last_t: float
    missed: int = 0


class Tracker:
    def __init__(self, max_speed_px_s=400.0, min_gate_px=20.0, max_missed=3, min_step_px=8.0):
        self.max_speed_px_s = max_speed_px_s  # gate = max(min_gate_px, max_speed * dt)
        self.min_gate_px = min_gate_px
        self.max_missed = max_missed
        self.min_step_px = min_step_px
        self.tracks = []

    def reset(self):
        self.tracks = []

    def update(self, points, t_s):
        """points: [(x, y)] for one processed frame at time t_s (seconds).
        Returns the step segments to accumulate: [((x0, y0), (x1, y1))]."""
        pairs = []
        for ti, tr in enumerate(self.tracks):
            gate = max(self.min_gate_px, self.max_speed_px_s * max(0.0, t_s - tr.last_t))
            for pi, (x, y) in enumerate(points):
                d = math.hypot(x - tr.x, y - tr.y)
                if d <= gate:
                    pairs.append((d, ti, pi))
        pairs.sort()  # greedy: closest pairs first, each track and point used once
        used_t, used_p, steps = set(), set(), []
        for d, ti, pi in pairs:
            if ti in used_t or pi in used_p:
                continue
            used_t.add(ti)
            used_p.add(pi)
            tr = self.tracks[ti]
            tr.x, tr.y = points[pi]
            tr.last_t, tr.missed = t_s, 0
            if math.hypot(tr.x - tr.ax, tr.y - tr.ay) >= self.min_step_px:
                steps.append(((tr.ax, tr.ay), (tr.x, tr.y)))
                tr.ax, tr.ay = tr.x, tr.y
        for ti, tr in enumerate(self.tracks):
            if ti not in used_t:
                tr.missed += 1
        self.tracks = [tr for tr in self.tracks if tr.missed <= self.max_missed]
        for pi, (x, y) in enumerate(points):
            if pi not in used_p:
                self.tracks.append(Tracklet(x, y, x, y, t_s))
        return steps
