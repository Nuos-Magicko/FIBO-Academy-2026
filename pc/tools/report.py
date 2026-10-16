"""Markdown tables from a baseline folder and (optionally) a soak folder.

  python pc/main.py report baselines/<baseline_dir>
  python pc/main.py report baselines/<baseline_dir> --soak runs/<soak_dir>

Per mode: mean ± sample SD across repeats (n = repeats) of each trial's value.
Stage times are the sample-weighted means from summary.csv. No interpretation
is added; the tables only restate the data.
"""

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any

from connection import protocol

STAGE_COLUMNS = (
    "t_wait_frame",
    "t_decode",
    "t_pre",
    "t_infer",
    "t_post",
    "t_proc",
)
SOAK_ROW_EVERY_MIN = 5
MISSING = "–"


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def table_row(cells: list[str]) -> str:
    """["a", "b"] -> "| a | b |", one row of a Markdown table."""
    return "| " + " | ".join(cells) + " |"


def table_separator(column_count: int) -> str:
    """The "|---|---|" line under a Markdown table's header."""
    return "|" + "---|" * column_count


def mean_sd(
    rows: list[dict[str, str]], key: str, scale: float = 1.0, decimals: int = 1
) -> str:
    """The text "mean ± SD" over the rows.

    Just the value when there is one row, and "–" when there is none.
    """
    values = []
    for row in rows:
        value = number(row[key])
        if value is not None:
            values.append(value * scale)
    if not values:
        return MISSING
    if len(values) == 1:
        return f"{values[0]:.{decimals}f}"
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    return f"{mean:.{decimals}f} ± {sd:.{decimals}f}"


def provenance_line(provenance: dict[str, Any]) -> str:
    device = provenance["device"]
    config = provenance["config"]
    device_part = ", ".join(
        [
            f"device `{device['device_id']}` (MAC {device['mac']})",
            f"sensor {device['sensor_pid']}",
            f"fw `{device['fw_version']}`",
            f"IDF {device['idf']}",
            f"esp-dl {device['esp_dl']}",
            f"pedestrian_detect {device['pedestrian_detect']}",
            f"esp32-camera {device['esp32_camera']}",
        ]
    )
    scene_part = ", ".join(
        [
            f"scene `{provenance['scene']}`",
            f"heatsink {provenance['heatsink']}",
            f"room temp {provenance['room_temp_c']}",
        ]
    )
    timing_part = (
        f"{provenance['repeats']} repeats × {provenance['seconds']:.0f} s"
        f" after {provenance['warmup_s']:.0f} s warm-up"
        f" (initial {provenance['initial_warmup_s']:.0f} s)"
    )
    config_part = ", ".join(
        [
            f"config {config['frame_size']} q{config['jpeg_quality']}",
            f"xclk {config['xclk_hz']}",
            f"infer_every_n {config['infer_every_n']}",
        ]
    )
    parts = [device_part, scene_part, timing_part, config_part]
    parts.append(provenance["date"])
    return "Provenance: " + "; ".join(parts) + "."


def throughput_table(by_mode: dict[str, list[dict[str, str]]]) -> list[str]:
    header = [
        "Mode",
        "processed",
        "sent",
        "camera capture",
        "received pairs/s",
        "received det-only/s",
    ]
    lines = [
        "### Throughput (frames per second; mean ± SD over repeats)",
        "",
        table_row(header),
        table_separator(len(header)),
    ]
    for mode, rows in by_mode.items():
        per_second = 1 / float(rows[0]["seconds"])
        cells = [
            f"`{mode}`",
            mean_sd(rows, "fps_processed_mean", decimals=2),
            mean_sd(rows, "fps_sent_mean", decimals=2),
            mean_sd(rows, "capture_fps", decimals=2),
            mean_sd(rows, "rx_pairs", per_second, 2),
            mean_sd(rows, "rx_det_only", per_second, 2),
        ]
        lines.append(table_row(cells))
    return lines


def stage_table(by_mode: dict[str, list[dict[str, str]]]) -> list[str]:
    header = ["Mode"] + list(STAGE_COLUMNS) + ["t_proc p95*", "t_send"]
    lines = [
        "### Per-frame stage times (ms; sample-weighted mean per trial,"
        " then mean ± SD over repeats)",
        "",
        table_row(header),
        table_separator(len(header)),
    ]
    for mode, rows in by_mode.items():
        cells = [f"`{mode}`"]
        for stage in STAGE_COLUMNS:
            cells.append(mean_sd(rows, f"{stage}_mean_us", 1e-3, 2))
        cells.append(mean_sd(rows, "t_proc_p95_mean_us", 1e-3, 2))
        cells.append(mean_sd(rows, "t_send_mean_us", 1e-3, 1))
        lines.append(table_row(cells))
    lines.append("")
    lines.append(
        "\\* mean of the per-window (1 s) p95 values,"
        " not a p95 over all frames."
    )
    return lines


def losses_table(by_mode: dict[str, list[dict[str, str]]]) -> list[str]:
    header = [
        "Mode",
        "tx_dropped",
        "frame_id gaps",
        "missing pairs",
        "seq gaps",
        "CRC errors",
        "die temp end (°C)",
        "die temp max (°C)",
        "int_min end (B)",
        "RSSI (dBm)",
    ]
    lines = [
        "### Losses, temperature, memory, radio"
        " (per trial; mean ± SD over repeats)",
        "",
        table_row(header),
        table_separator(len(header)),
    ]
    for mode, rows in by_mode.items():
        cells = [
            f"`{mode}`",
            mean_sd(rows, "tx_dropped"),
            mean_sd(rows, "frame_id_gaps"),
            mean_sd(rows, "missing_pairs"),
            mean_sd(rows, "seq_gaps"),
            mean_sd(rows, "crc_errors"),
            mean_sd(rows, "temp_end_c"),
            mean_sd(rows, "temp_max_c"),
            mean_sd(rows, "int_min_end", decimals=0),
            mean_sd(rows, "rssi_mean"),
        ]
        lines.append(table_row(cells))
    return lines


def baseline_tables(folder: Path) -> list[str]:
    provenance = json.loads((folder / "baseline.json").read_text())
    with open(folder / "summary.csv") as summary_file:
        rows = list(csv.DictReader(summary_file))
    by_mode = {}
    for mode in protocol.MODES:
        trials = []
        for row in rows:
            if row["mode"] == mode:
                trials.append(row)
        if trials:
            by_mode[mode] = trials
    lines = [provenance_line(provenance), ""]
    lines += throughput_table(by_mode)
    lines += [""]
    lines += stage_table(by_mode)
    lines += [""]
    lines += losses_table(by_mode)
    return lines


def soak_header(summary: dict[str, Any]) -> str:
    parts = [
        f"Soak `{summary['label']}`: {summary['duration_s']} s"
        f" from {summary['started']}",
        f"device boots seen {summary['device_boots_seen']}"
        f" (unexpected resets {summary['unexpected_resets']},"
        f" reasons {summary['reset_reasons']})",
        f"frames received {summary['frames_received']}"
        f" ({summary['rx_fps_mean']} fps mean,"
        f" {summary['rx_bytes_per_s_mean']} B/s)",
        f"seq gaps {summary['seq_gaps']}, CRC errors {summary['crc_errors']},"
        f" missing pairs {summary['missing_pairs']},"
        f" reconnects {summary['reconnects']}",
    ]
    return "; ".join(parts) + "."


def soak_row(window: dict[str, Any], elapsed_min: float) -> str:
    cells = [
        f"{elapsed_min:.0f}",
        f"{window.get('temp_c', 0):.1f}",
        str(window.get("mem.int_free")),
        str(window.get("mem.int_min")),
        str(window.get("mem.ps_min")),
        f"{window.get('fps_processed', 0):.2f}",
        f"{window.get('fps_sent', 0):.2f}",
        str(window.get("counters.tx_dropped")),
        str(window.get("rssi")),
    ]
    return table_row(cells)


def soak_tables(folder: Path) -> list[str]:
    summary = json.loads((folder / "summary.json").read_text())
    with open(folder / "metrics.jsonl") as metrics_file:
        windows = [json.loads(line) for line in metrics_file]
    header = [
        "Elapsed (min)",
        "die temp (°C)",
        "int_free (B)",
        "int_min (B)",
        "ps_min (B)",
        "fps processed",
        "fps sent",
        "tx_dropped (cum.)",
        "RSSI",
    ]
    lines = [
        soak_header(summary),
        "",
        table_row(header),
        table_separator(len(header)),
    ]
    start_s = windows[0].get("uptime_s", 0)
    next_row_min = 0.0
    for window in windows:
        elapsed_min = (window.get("uptime_s", 0) - start_s) / 60
        if elapsed_min + 1e-9 >= next_row_min:
            lines.append(soak_row(window, elapsed_min))
            next_row_min += SOAK_ROW_EVERY_MIN
    last = windows[-1]
    final_row = soak_row(last, (last.get("uptime_s", 0) - start_s) / 60)
    if lines[-1] != final_row:
        lines.append(final_row)
    send_maximums = []
    temps = []
    windows_with_drops = 0
    for window in windows:
        if window.get("stages.t_send.max") is not None:
            send_maximums.append(window["stages.t_send.max"])
        if window.get("temp_c") is not None:
            temps.append(window["temp_c"])
        dropped = window.get("dropped_in_window")
        if dropped is not None and dropped > 0:
            windows_with_drops += 1
    worst_send = max(send_maximums)
    lines.append("")
    lines.append(
        f"Over the soak: die temp min/max {min(temps):.1f}/{max(temps):.1f}"
        f" °C; worst per-window t_send max {worst_send / 1e3:.0f} ms;"
        f" windows with any drop {windows_with_drops} of {len(windows)}."
    )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--soak", type=Path)
    args = parser.parse_args()
    lines = baseline_tables(args.baseline)
    if args.soak:
        lines += ["", "### Soak", ""] + soak_tables(args.soak)
    print("\n".join(lines))
    return 0
