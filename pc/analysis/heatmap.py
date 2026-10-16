"""The two heatmaps. They measure different things on purpose.

The picture is divided into 20 x 15 cells.

1. Presence ("where people were"), counted ON THE BOARD: for every processed frame, +1 in the
   cell under each person's feet. Divide by the number of frames (or seconds) to compare runs.
2. Movement ("where people moved"), worked out ON THE LAPTOP: every time a tracklet takes a step
   (tracker.py), +1 in every cell the step crosses. Divide by the minutes watched.

Neither one is a floor plan (the camera angle stretches distances) or a count of people.
"""
import cv2
import numpy as np

COLS, ROWS = 20, 15
OVERLAY_ALPHA = 0.45

LIMITATIONS = ("Image space, not a floor plan (perspective skew). Stationary people dominate presence. "
               "Missed detections break paths. A tracklet is not a person count. "
               "Changing frame rate invalidates raw count comparisons.")


def cell_of(x, y, width, height, cols=COLS, rows=ROWS):
    """(row, col) of a pixel, clamped to the frame: the same mapping as firmware grid.cpp."""
    x = min(max(int(x), 0), width - 1)
    y = min(max(int(y), 0), height - 1)
    return y * rows // height, x * cols // width


def bottom_center(box):
    """The point a box contributes to both heatmaps: where the person stands."""
    return ((box["x1"] + box["x2"]) // 2, box["y2"])


def presence_add(counts, boxes, width, height):
    """Python mirror of firmware grid_add(), for synthetic data and tests."""
    rows, cols = counts.shape
    for box in boxes:
        row, col = cell_of(*bottom_center(box), width, height, cols, rows)
        counts[row, col] += 1


def normalize_presence(grid, per="frame"):
    """Decoded PRESENCE_GRID -> detections per processed frame (per="frame") or per second
    (per="second"), per cell."""
    counts = np.asarray(grid["counts"], dtype=float)
    if per == "frame":
        denominator = grid["frames"]
    elif per == "second":
        denominator = grid["elapsed_us"] / 1e6
    else:
        raise ValueError(per)
    return counts / denominator if denominator > 0 else np.zeros_like(counts)


class MovementGrid:
    """Accumulates tracklet step segments as cell crossings, plus the time actually observed."""

    def __init__(self, w, h, cols=COLS, rows=ROWS):
        self.w, self.h, self.cols, self.rows = w, h, cols, rows
        self.counts = np.zeros((rows, cols), dtype=np.int64)
        self.observed_s = 0.0

    def reset(self, w=None, h=None):
        self.w, self.h = w or self.w, h or self.h
        self.counts[:] = 0
        self.observed_s = 0.0

    def add_segment(self, start, end):
        """+1 for every cell the segment passes through, each cell at most once per segment.

        The segment is sampled at half-cell spacing, which cannot skip a cell.
        """
        cell_width, cell_height = self.w / self.cols, self.h / self.rows
        length = np.hypot(end[0] - start[0], end[1] - start[1])
        samples = max(2, int(np.ceil(length / (min(cell_width, cell_height) / 2))) + 1)
        cells = {cell_of(start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t, self.w, self.h,
                         self.cols, self.rows) for t in np.linspace(0, 1, samples)}
        for row, col in cells:
            self.counts[row, col] += 1

    def per_minute(self):
        """Step crossings per minute of observed time, per cell."""
        if self.observed_s <= 0:
            return np.zeros(self.counts.shape)
        return self.counts / (self.observed_s / 60)


def overlay(image, values, alpha=OVERLAY_ALPHA):
    """Blend a rows x cols grid over a BGR image, scaled to the grid's own maximum.

    Cells with zero stay untouched. Returns (blended image, maximum value).
    """
    vmax = float(values.max()) if values.size else 0.0
    if vmax <= 0:
        return image, 0.0
    levels = np.uint8(np.clip(values / vmax, 0, 1) * 255)
    levels = cv2.resize(levels, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)
    heat = cv2.applyColorMap(levels, cv2.COLORMAP_JET)
    nonzero = levels > 0
    blended = image.copy()
    blended[nonzero] = cv2.addWeighted(image, 1 - alpha, heat, alpha, 0)[nonzero]
    return blended, vmax
