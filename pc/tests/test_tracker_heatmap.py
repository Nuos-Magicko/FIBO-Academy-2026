import numpy as np
import pytest

from heatmap import MovementGrid, cell_of, normalize_presence, presence_add
from tracker import Tracker


# ---------------------------------------------------------------- presence grid

def test_cell_mapping_matches_firmware_and_clamps():
    assert cell_of(0, 0, 320, 240) == (0, 0)
    assert cell_of(319, 239, 320, 240) == (14, 19)
    assert cell_of(16, 16, 320, 240) == (1, 1)  # 320/20 = 16 px per column, 240/15 = 16 px per row
    assert cell_of(-5, 999, 320, 240) == (14, 0)  # clamped like grid.cpp


def test_presence_uses_bottom_center():
    counts = np.zeros((15, 20), int)
    presence_add(counts, [{"x1": 0, "y1": 0, "x2": 40, "y2": 239}], 320, 240)
    assert counts[14, 1] == 1 and counts.sum() == 1  # bottom-center (20, 239)


def test_presence_normalization():
    g = {"counts": [[0, 6], [3, 0]], "frames": 3, "elapsed_us": 2_000_000}
    assert normalize_presence(g, "frame").tolist() == [[0, 2], [1, 0]]
    assert normalize_presence(g, "second").tolist() == [[0, 3], [1.5, 0]]
    assert normalize_presence({**g, "frames": 0}, "frame").sum() == 0
    with pytest.raises(ValueError):
        normalize_presence(g, "minute")


# ---------------------------------------------------------------- tracker

def test_tracker_links_nearest_and_emits_steps():
    t = Tracker(max_speed_px_s=400, min_gate_px=20, max_missed=2, min_step_px=8)
    assert t.update([(100, 100), (200, 100)], 0.0) == []  # new tracklets, no steps yet
    steps = t.update([(210, 100), (110, 100)], 0.1)  # both moved 10 px
    assert sorted(steps) == [((100, 100), (110, 100)), ((200, 100), (210, 100))]
    assert len(t.tracks) == 2


def test_tracker_suppresses_jitter_but_keeps_slow_motion():
    t = Tracker(min_step_px=8)
    t.update([(100, 100)], 0.0)
    assert t.update([(103, 100)], 0.1) == []  # 3 px: jitter, no step
    assert t.update([(105, 100)], 0.2) == []  # 5 px from anchor
    assert t.update([(109, 100)], 0.3) == [((100, 100), (109, 100))]  # 9 px from anchor: one step


def test_tracker_gate_rejects_far_points_and_expires():
    t = Tracker(max_speed_px_s=100, min_gate_px=20, max_missed=1)
    t.update([(0, 0)], 0.0)
    assert t.update([(200, 0)], 0.1) == []  # 200 px in 0.1 s > gate: new tracklet, old one missed
    assert len(t.tracks) == 2
    t.update([(200, 0)], 0.2)  # (0,0) missed twice -> expired
    assert [(tr.x, tr.y) for tr in t.tracks] == [(200, 0)]


def test_tracker_each_point_used_once():
    t = Tracker(min_step_px=1)
    t.update([(100, 100), (104, 100)], 0.0)
    steps = t.update([(102, 100)], 0.1)
    assert len(steps) == 1 and len(t.tracks) == 2  # one track matched, the other missed


# ---------------------------------------------------------------- movement grid

def test_segment_rasterization_counts_each_cell_once():
    m = MovementGrid(320, 240)
    m.add_segment((8, 8), (8 + 16 * 3, 8))  # crosses columns 0..3 in row 0
    assert m.counts[0, :4].tolist() == [1, 1, 1, 1] and m.counts.sum() == 4
    m.add_segment((8, 8), (8, 8))  # zero-length: its own cell once
    assert m.counts[0, 0] == 2


def test_movement_per_minute_normalization():
    m = MovementGrid(320, 240)
    m.add_segment((8, 8), (8, 8))
    assert m.per_minute().sum() == 0  # no observed time yet
    m.observed_s = 30
    assert m.per_minute()[0, 0] == 2.0
