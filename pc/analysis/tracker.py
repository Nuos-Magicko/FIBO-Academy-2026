"""Short movement paths ("tracklets") for the movement heatmap.

Each person is a point: the middle of the bottom edge of their box, roughly
where their feet are. From one frame to the next, every point is linked to the
nearest point of the previous frame, guessing that it is the same person who
moved a little. Nothing else is used: no faces, no colours, no names. If a path
finds no match for a few frames (max_missed), it is forgotten.

Small wobbles are ignored. A path only counts a "step" once it has moved at
least min_step_px pixels from where its last step ended (its anchor). So a
person standing still, whose box wobbles by a few pixels, adds nothing.

A tracklet is NOT a person: one person can produce several short paths when the
AI misses them for a frame.
"""

import math
from dataclasses import dataclass

Point = tuple[float, float]
"""An (x, y) position in pixels."""
Step = tuple[Point, Point]
"""A move from one point to another."""


@dataclass
class Tracklet:
    """Latest position (x, y), and the anchor (anchor_x, anchor_y) where the
    last emitted step ended."""

    x: float
    y: float
    anchor_x: float
    anchor_y: float
    last_t: float
    missed: int = 0


class Tracker:
    """Greedy nearest-neighbour association with a speed gate.

    A point may join a tracklet if it is within max(min_gate_px, max_speed_px_s
    * dt) of it.
    """

    def __init__(
        self,
        max_speed_px_s: float = 400.0,
        min_gate_px: float = 20.0,
        max_missed: int = 3,
        min_step_px: float = 8.0,
    ) -> None:
        self.max_speed_px_s = max_speed_px_s
        self.min_gate_px = min_gate_px
        self.max_missed = max_missed
        self.min_step_px = min_step_px
        self.tracks: list[Tracklet] = []

    def reset(self) -> None:
        self.tracks = []

    def update(self, points: list[Point], t_s: float) -> list[Step]:
        """Associate one processed frame's points [(x, y)] at time t_s
        (seconds).

        Returns the step segments to accumulate: [((x0, y0), (x1, y1))].
        """
        matched_tracks = set()
        matched_points = set()
        steps = []
        for distance, track_index, point_index in self._candidate_pairs(
            points, t_s
        ):
            if track_index in matched_tracks:
                continue
            if point_index in matched_points:
                continue
            matched_tracks.add(track_index)
            matched_points.add(point_index)
            step = self._move(
                self.tracks[track_index], points[point_index], t_s
            )
            if step is not None:
                steps.append(step)
        self._age_unmatched(matched_tracks)

        for point_index, point in enumerate(points):
            if point_index not in matched_points:
                x, y = point
                self.tracks.append(Tracklet(x, y, x, y, t_s))
        return steps

    def _candidate_pairs(
        self, points: list[Point], t_s: float
    ) -> list[tuple[float, int, int]]:
        """(distance, track index, point index) for every pair inside the gate,
        closest first."""
        pairs = []
        for track_index, track in enumerate(self.tracks):
            seconds_since_seen = max(0.0, t_s - track.last_t)
            gate = max(
                self.min_gate_px, self.max_speed_px_s * seconds_since_seen
            )
            for point_index, point in enumerate(points):
                x, y = point
                distance = math.hypot(x - track.x, y - track.y)
                if distance <= gate:
                    pairs.append((distance, track_index, point_index))
        pairs.sort()
        return pairs

    def _move(self, track: Tracklet, point: Point, t_s: float) -> Step | None:
        """Move the tracklet to point; return a step if it is now min_step_px
        from its anchor."""
        x, y = point
        track.x = x
        track.y = y
        track.last_t = t_s
        track.missed = 0
        distance_from_anchor = math.hypot(
            track.x - track.anchor_x, track.y - track.anchor_y
        )
        if distance_from_anchor < self.min_step_px:
            return None
        step = ((track.anchor_x, track.anchor_y), (track.x, track.y))
        track.anchor_x = track.x
        track.anchor_y = track.y
        return step

    def _age_unmatched(self, matched_tracks: set[int]) -> None:
        for index, track in enumerate(self.tracks):
            if index not in matched_tracks:
                track.missed += 1
        kept = []
        for track in self.tracks:
            if track.missed <= self.max_missed:
                kept.append(track)
        self.tracks = kept
