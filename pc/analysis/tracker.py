"""Short movement paths ("tracklets") for the movement heatmap.

Each person is a point: the middle of the bottom edge of their box, roughly where their feet
are. From one frame to the next, every point is linked to the nearest point of the previous
frame, guessing that it is the same person who moved a little. Nothing else is used: no faces,
no colours, no names. If a path finds no match for a few frames (max_missed), it is forgotten.

Small wobbles are ignored. A path only counts a "step" once it has moved at least min_step_px
pixels from where its last step ended (its anchor). So a person standing still, whose box
wobbles by a few pixels, adds nothing.

A tracklet is NOT a person: one person can produce several short paths when the AI misses them
for a frame.
"""
import math
from dataclasses import dataclass


@dataclass
class Tracklet:
    """Latest position (x, y), and the anchor (anchor_x, anchor_y) where the last emitted step ended."""

    x: float
    y: float
    anchor_x: float
    anchor_y: float
    last_t: float
    missed: int = 0


class Tracker:
    """Greedy nearest-neighbour association with a speed gate.

    A point may join a tracklet if it is within max(min_gate_px, max_speed_px_s * dt) of it.
    """

    def __init__(self, max_speed_px_s=400.0, min_gate_px=20.0, max_missed=3, min_step_px=8.0):
        self.max_speed_px_s = max_speed_px_s
        self.min_gate_px = min_gate_px
        self.max_missed = max_missed
        self.min_step_px = min_step_px
        self.tracks = []

    def reset(self):
        self.tracks = []

    def update(self, points, t_s):
        """Associate one processed frame's points [(x, y)] at time t_s (seconds).

        Returns the step segments to accumulate: [((x0, y0), (x1, y1))].
        """
        matched_tracks, matched_points, steps = set(), set(), []
        for _, track_index, point_index in self._candidate_pairs(points, t_s):
            if track_index in matched_tracks or point_index in matched_points:
                continue
            matched_tracks.add(track_index)
            matched_points.add(point_index)
            step = self._move(self.tracks[track_index], points[point_index], t_s)
            if step:
                steps.append(step)
        self._age_unmatched(matched_tracks)
        self.tracks += [Tracklet(x, y, x, y, t_s) for i, (x, y) in enumerate(points) if i not in matched_points]
        return steps

    def _candidate_pairs(self, points, t_s):
        """(distance, track index, point index) for every pair inside the gate, closest first."""
        pairs = []
        for track_index, track in enumerate(self.tracks):
            gate = max(self.min_gate_px, self.max_speed_px_s * max(0.0, t_s - track.last_t))
            for point_index, (x, y) in enumerate(points):
                distance = math.hypot(x - track.x, y - track.y)
                if distance <= gate:
                    pairs.append((distance, track_index, point_index))
        return sorted(pairs)

    def _move(self, track, point, t_s):
        """Move the tracklet to point; return a step if it is now min_step_px from its anchor."""
        track.x, track.y = point
        track.last_t, track.missed = t_s, 0
        if math.hypot(track.x - track.anchor_x, track.y - track.anchor_y) < self.min_step_px:
            return None
        step = ((track.anchor_x, track.anchor_y), (track.x, track.y))
        track.anchor_x, track.anchor_y = track.x, track.y
        return step

    def _age_unmatched(self, matched_tracks):
        for index, track in enumerate(self.tracks):
            if index not in matched_tracks:
                track.missed += 1
        self.tracks = [track for track in self.tracks if track.missed <= self.max_missed]
