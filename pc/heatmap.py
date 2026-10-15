"""Two different heatmap metrics, kept separate on purpose (handoff F3).

1. Presence intensity (sampled), computed ON THE DEVICE: +1 per box per processed frame in the
   cell holding the box's bottom-center point. Normalize by processed frames or by elapsed time.
2. Approximate image-space movement, computed ON THE PC from tracklet steps (tracker.py).

Neither is a foot-traffic count or a floor plan.
"""
import cv2
import numpy as np

COLS, ROWS = 20, 15

LIMITATIONS = ("Image space, not a floor plan (perspective skew). Stationary people dominate presence. "
               "Missed detections break paths. A tracklet is not a person count. "
               "Changing frame rate invalidates raw count comparisons.")


def cell_of(x, y, w, h, cols=COLS, rows=ROWS):
    """Same mapping as firmware grid.cpp: clamp to the frame, integer cell index."""
    x = min(max(int(x), 0), w - 1)
    y = min(max(int(y), 0), h - 1)
    return y * rows // h, x * cols // w


def bottom_center(box):
    return ((box["x1"] + box["x2"]) // 2, box["y2"])


def presence_add(counts, boxes, w, h):
    """Python mirror of firmware grid_add(), for synthetic data and tests."""
    for b in boxes:
        r, c = cell_of(*bottom_center(b), w, h, counts.shape[1], counts.shape[0])
        counts[r, c] += 1


def normalize_presence(grid, per="frame"):
    """grid: decoded PRESENCE_GRID dict. per='frame' -> detections per processed frame per cell;
    per='second' -> detections per second per cell."""
    counts = np.asarray(grid["counts"], dtype=float)
    if per == "frame":
        denom = grid["frames"]
    elif per == "second":
        denom = grid["elapsed_us"] / 1e6
    else:
        raise ValueError(per)
    return counts / denom if denom > 0 else np.zeros_like(counts)


class MovementGrid:
    """Accumulates step segments (from tracker.Tracker) as cell crossings."""

    def __init__(self, w, h, cols=COLS, rows=ROWS):
        self.w, self.h, self.cols, self.rows = w, h, cols, rows
        self.counts = np.zeros((rows, cols), dtype=np.int64)
        self.observed_s = 0.0

    def reset(self, w=None, h=None):
        self.w, self.h = w or self.w, h or self.h
        self.counts[:] = 0
        self.observed_s = 0.0

    def add_segment(self, p0, p1):
        """+1 for every cell the segment p0 -> p1 passes through (each cell once per segment)."""
        cell_w, cell_h = self.w / self.cols, self.h / self.rows
        length = np.hypot(p1[0] - p0[0], p1[1] - p0[1])
        n = max(2, int(np.ceil(length / (min(cell_w, cell_h) / 2))) + 1)  # sample at half-cell spacing
        cells = {cell_of(p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t, self.w, self.h,
                         self.cols, self.rows) for t in np.linspace(0, 1, n)}
        for r, c in cells:
            self.counts[r, c] += 1

    def per_minute(self):
        """Step crossings per minute of observed time, per cell."""
        return self.counts / (self.observed_s / 60) if self.observed_s > 0 else np.zeros(self.counts.shape)


def overlay(img, values, alpha=0.45):
    """Blend a cols x rows value grid over img (BGR). Scaled to the grid's own max; returns (img, max)."""
    vmax = float(values.max()) if values.size else 0.0
    if vmax <= 0:
        return img, 0.0
    norm = np.uint8(np.clip(values / vmax, 0, 1) * 255)
    heat = cv2.applyColorMap(cv2.resize(norm, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST),
                             cv2.COLORMAP_JET)
    mask = cv2.resize(norm, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    out = img.copy()
    out[mask] = cv2.addWeighted(img, 1 - alpha, heat, alpha, 0)[mask]
    return out, vmax
