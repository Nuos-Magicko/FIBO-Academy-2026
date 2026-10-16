"""Presence-grid cell mapping and normalization, tracklet association, movement
accumulation.

The QVGA grid has 16 x 16 px cells (320 / 20 columns, 240 / 15 rows).
"""

import numpy as np
import pytest

from analysis.heatmap import (
    MovementGrid,
    cell_of,
    normalize_presence,
    presence_add,
)
from analysis.tracker import Tracker

CELL_PX = 16


def test_cell_mapping_matches_firmware() -> None:
    assert cell_of(0, 0, 320, 240) == (0, 0)
    assert cell_of(319, 239, 320, 240) == (14, 19)
    assert cell_of(CELL_PX, CELL_PX, 320, 240) == (1, 1)


def test_cell_mapping_clamps_like_grid_cpp() -> None:
    assert cell_of(-5, 999, 320, 240) == (14, 0)


def test_presence_counts_the_bottom_center_point() -> None:
    """Box bottom-center (20, 239) is in row 14, column 1."""
    counts = np.zeros((15, 20), int)
    presence_add(counts, [{"x1": 0, "y1": 0, "x2": 40, "y2": 239}], 320, 240)
    assert counts[14, 1] == 1
    assert counts.sum() == 1


def test_presence_normalization() -> None:
    grid = {"counts": [[0, 6], [3, 0]], "frames": 3, "elapsed_us": 2_000_000}
    assert normalize_presence(grid, "frame").tolist() == [[0, 2], [1, 0]]
    assert normalize_presence(grid, "second").tolist() == [[0, 3], [1.5, 0]]
    grid_without_frames = dict(grid)
    grid_without_frames["frames"] = 0
    assert normalize_presence(grid_without_frames, "frame").sum() == 0
    with pytest.raises(ValueError):
        normalize_presence(grid, "minute")


def test_tracker_links_nearest_points_and_emits_steps() -> None:
    tracker = Tracker(
        max_speed_px_s=400, min_gate_px=20, max_missed=2, min_step_px=8
    )
    assert tracker.update([(100, 100), (200, 100)], 0.0) == []
    steps = tracker.update([(210, 100), (110, 100)], 0.1)
    assert sorted(steps) == [
        ((100, 100), (110, 100)),
        ((200, 100), (210, 100)),
    ]
    assert len(tracker.tracks) == 2


def test_tracker_suppresses_jitter_but_keeps_slow_motion() -> None:
    """3 px and 5 px from the anchor are jitter; at 9 px one step from the
    anchor is emitted."""
    tracker = Tracker(min_step_px=8)
    tracker.update([(100, 100)], 0.0)
    assert tracker.update([(103, 100)], 0.1) == []
    assert tracker.update([(105, 100)], 0.2) == []
    assert tracker.update([(109, 100)], 0.3) == [((100, 100), (109, 100))]


def test_tracker_gate_rejects_far_points_and_tracklets_expire() -> None:
    """200 px in 0.1 s exceeds a 100 px/s gate: a new tracklet starts, and the
    old one expires after missing twice with max_missed=1."""
    tracker = Tracker(max_speed_px_s=100, min_gate_px=20, max_missed=1)
    tracker.update([(0, 0)], 0.0)
    assert tracker.update([(200, 0)], 0.1) == []
    assert len(tracker.tracks) == 2
    tracker.update([(200, 0)], 0.2)
    assert [(track.x, track.y) for track in tracker.tracks] == [(200, 0)]


def test_tracker_uses_each_point_once() -> None:
    tracker = Tracker(min_step_px=1)
    tracker.update([(100, 100), (104, 100)], 0.0)
    steps = tracker.update([(102, 100)], 0.1)
    assert len(steps) == 1
    assert len(tracker.tracks) == 2


def test_segment_counts_each_crossed_cell_once() -> None:
    movement = MovementGrid(320, 240)
    movement.add_segment((8, 8), (8 + CELL_PX * 3, 8))
    assert movement.counts[0, :4].tolist() == [1, 1, 1, 1]
    assert movement.counts.sum() == 4


def test_zero_length_segment_counts_its_own_cell() -> None:
    movement = MovementGrid(320, 240)
    movement.add_segment((8, 8), (8, 8))
    assert movement.counts[0, 0] == 1
    assert movement.counts.sum() == 1


def test_movement_per_minute_needs_observed_time() -> None:
    movement = MovementGrid(320, 240)
    movement.add_segment((8, 8), (8, 8))
    movement.add_segment((8, 8), (8, 8))
    assert movement.per_minute().sum() == 0
    movement.observed_s = 30
    assert movement.per_minute()[0, 0] == 4.0
