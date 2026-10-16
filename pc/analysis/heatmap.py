"""The two heatmaps. They measure different things on purpose.

The picture is divided into 20 x 15 cells.

1. Presence ("where people were"), counted ON THE BOARD: for every processed
   frame, +1 in the cell under each person's feet. Divide by the number of
   frames (or seconds) to compare runs.
2. Movement ("where people moved"), worked out ON THE LAPTOP: every time a
   tracklet takes a step (tracker.py), +1 in every cell the step crosses.
   Divide by the minutes watched.

Neither one is a floor plan (the camera angle stretches distances) or a count
of people.
"""

from typing import Any
import cv2
import numpy as np

COLS = 20
ROWS = 15
OVERLAY_ALPHA = 0.45

LIMITATIONS = (
    "Image space, not a floor plan (perspective skew). "
    "Stationary people dominate presence. "
    "Missed detections break paths. A tracklet is not a person count. "
    "Changing frame rate invalidates raw count comparisons."
)


def clamp(value: int, lowest: int, highest: int) -> int:
    """Keeps value inside lowest..highest."""
    if value < lowest:
        return lowest
    if value > highest:
        return highest
    return value


def cell_of(
    x: float,
    y: float,
    width: int,
    height: int,
    cols: int = COLS,
    rows: int = ROWS,
) -> tuple[int, int]:
    """(row, col) of a pixel, clamped to the frame: the same mapping as
    firmware grid.cpp."""
    x = clamp(int(x), 0, width - 1)
    y = clamp(int(y), 0, height - 1)
    row = y * rows // height
    col = x * cols // width
    return row, col


def bottom_center(box: dict[str, Any]) -> tuple[int, int]:
    """The point a box adds to both heatmaps: where the person stands."""
    x = (box["x1"] + box["x2"]) // 2
    y = box["y2"]
    return (x, y)


def presence_add(
    counts: np.ndarray, boxes: list[dict[str, Any]], width: int, height: int
) -> None:
    """Python mirror of firmware grid_add(), for synthetic data and tests."""
    rows, cols = counts.shape
    for box in boxes:
        x, y = bottom_center(box)
        row, col = cell_of(x, y, width, height, cols, rows)
        counts[row, col] += 1


def normalize_presence(grid: dict[str, Any], per: str = "frame") -> np.ndarray:
    """Decoded PRESENCE_GRID -> detections per processed frame (per="frame") or
    per second (per="second"), per cell."""
    counts = np.asarray(grid["counts"], dtype=float)
    if per == "frame":
        denominator = grid["frames"]
    elif per == "second":
        denominator = grid["elapsed_us"] / 1e6
    else:
        raise ValueError(per)
    if denominator > 0:
        return counts / denominator
    return np.zeros_like(counts)


class MovementGrid:
    """Accumulates tracklet step segments as cell crossings, plus the time
    actually observed."""

    def __init__(
        self, width: int, height: int, cols: int = COLS, rows: int = ROWS
    ) -> None:
        self.width = width
        self.height = height
        self.cols = cols
        self.rows = rows
        self.counts = np.zeros((rows, cols), dtype=np.int64)
        self.observed_s = 0.0

    def reset(self) -> None:
        self.counts[:] = 0
        self.observed_s = 0.0

    def add_segment(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> None:
        """+1 for every cell the segment passes through, each cell at most once
        per segment.

        The segment is sampled at half-cell spacing, which cannot skip a cell.
        """
        cell_width = self.width / self.cols
        cell_height = self.height / self.rows
        start_x, start_y = start
        end_x, end_y = end
        length = np.hypot(end_x - start_x, end_y - start_y)
        spacing = min(cell_width, cell_height) / 2
        samples = max(2, int(np.ceil(length / spacing)) + 1)

        cells = set()
        for fraction in np.linspace(0, 1, samples):
            x = start_x + (end_x - start_x) * fraction
            y = start_y + (end_y - start_y) * fraction
            cells.add(
                cell_of(x, y, self.width, self.height, self.cols, self.rows)
            )
        for row, col in cells:
            self.counts[row, col] += 1

    def per_minute(self) -> np.ndarray:
        """Step crossings per minute of observed time, per cell."""
        if self.observed_s <= 0:
            return np.zeros(self.counts.shape)
        return self.counts / (self.observed_s / 60)


def overlay(
    image: np.ndarray, values: np.ndarray, alpha: float = OVERLAY_ALPHA
) -> tuple[np.ndarray, float]:
    """Blend a rows x cols grid over a BGR image, scaled to the grid's own
    maximum.

    Cells with zero stay untouched. Returns (blended image, maximum value).
    """
    if values.size == 0:
        return image, 0.0
    highest = float(values.max())
    if highest <= 0:
        return image, 0.0
    grid_levels = (np.clip(values / highest, 0, 1) * 255).astype(np.uint8)
    image_size = (image.shape[1], image.shape[0])
    levels = cv2.resize(
        grid_levels, image_size, interpolation=cv2.INTER_NEAREST
    )
    heat = cv2.applyColorMap(levels, cv2.COLORMAP_JET)
    nonzero = levels > 0
    blended = image.copy()
    blended[nonzero] = cv2.addWeighted(image, 1 - alpha, heat, alpha, 0)[
        nonzero
    ]
    return blended, highest
