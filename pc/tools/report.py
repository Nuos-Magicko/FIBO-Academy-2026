"""Markdown tables from a collect_baseline.py folder and (optionally) a soak.py run folder.

  python pc/main.py report docs/measurements/<baseline_dir> [--soak docs/measurements/<soak_dir>]

Per mode: mean ± sample SD across repeats (n = repeats) of each trial's value. Stage times are the
sample-weighted means from summary.csv. No interpretation is added; the tables only restate the data.
"""
import argparse
import csv
import json
import statistics
from pathlib import Path

MODES = ["full_stream", "metadata_stream", "detect_no_stream", "camera_only", "inference_only"]
STAGE_COLUMNS = ("t_wait_frame", "t_decode", "t_pre", "t_infer", "t_post", "t_proc")
SOAK_ROW_EVERY_MIN = 5
MISSING = "–"


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def mean_sd(rows, key, scale=1.0, decimals=1):
    """"mean ± SD" over the rows (just the value when there is one row, "–" when there is none)."""
    values = [number(row[key]) for row in rows]
    values = [value * scale for value in values if value is not None]
    if not values:
        return MISSING
    if len(values) == 1:
        return f"{values[0]:.{decimals}f}"
    return f"{statistics.fmean(values):.{decimals}f} ± {statistics.stdev(values):.{decimals}f}"


def provenance_line(provenance):
    device, config = provenance["device"], provenance["config"]
    return (f"Provenance: device `{device['device_id']}` (MAC {device['mac']}), sensor {device['sensor_pid']}, fw "
            f"`{device['fw_version']}`, IDF {device['idf']}, esp-dl {device['esp_dl']}, pedestrian_detect "
            f"{device['pedestrian_detect']}, esp32-camera {device['esp32_camera']}; scene `{provenance['scene']}`, "
            f"heatsink {provenance['heatsink']}, room temp {provenance['room_temp_c']}; {provenance['repeats']} "
            f"repeats × {provenance['seconds']:.0f} s after {provenance['warmup_s']:.0f} s warm-up (initial "
            f"{provenance['initial_warmup_s']:.0f} s); config {config['frame_size']} q{config['jpeg_quality']}, "
            f"xclk {config['xclk_hz']}, infer_every_n {config['infer_every_n']}; {provenance['date']}.")


def throughput_table(by_mode):
    lines = ["### Throughput (frames per second; mean ± SD over repeats)", "",
             "| Mode | processed | sent | camera capture | received pairs/s | received det-only/s |",
             "|---|---|---|---|---|---|"]
    for mode, rows in by_mode.items():
        per_second = 1 / number(rows[0]["seconds"])
        lines.append(f"| `{mode}` | {mean_sd(rows, 'fps_processed_mean', decimals=2)} | "
                     f"{mean_sd(rows, 'fps_sent_mean', decimals=2)} | {mean_sd(rows, 'capture_fps', decimals=2)} | "
                     f"{mean_sd(rows, 'rx_pairs', per_second, 2)} | {mean_sd(rows, 'rx_det_only', per_second, 2)} |")
    return lines


def stage_table(by_mode):
    lines = ["### Per-frame stage times (ms; sample-weighted mean per trial, then mean ± SD over repeats)", "",
             "| Mode | t_wait_frame | t_decode | t_pre | t_infer | t_post | t_proc | t_proc p95* | t_send |",
             "|---|---|---|---|---|---|---|---|---|"]
    for mode, rows in by_mode.items():
        cells = [mean_sd(rows, f"{stage}_mean_us", 1e-3, 2) for stage in STAGE_COLUMNS]
        cells += [mean_sd(rows, "t_proc_p95_mean_us", 1e-3, 2), mean_sd(rows, "t_send_mean_us", 1e-3, 1)]
        lines.append(f"| `{mode}` | " + " | ".join(cells) + " |")
    return lines + ["", "\\* mean of the per-window (1 s) p95 values, not a p95 over all frames."]


def losses_table(by_mode):
    lines = ["### Losses, temperature, memory, radio (per trial; mean ± SD over repeats)", "",
             "| Mode | tx_dropped | frame_id gaps | missing pairs | seq gaps | CRC errors | die temp end (°C) | "
             "die temp max (°C) | int_min end (B) | RSSI (dBm) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for mode, rows in by_mode.items():
        lines.append(f"| `{mode}` | {mean_sd(rows, 'tx_dropped')} | {mean_sd(rows, 'frame_id_gaps')} | "
                     f"{mean_sd(rows, 'missing_pairs')} | {mean_sd(rows, 'seq_gaps')} | {mean_sd(rows, 'crc_errors')} | "
                     f"{mean_sd(rows, 'temp_end_c')} | {mean_sd(rows, 'temp_max_c')} | "
                     f"{mean_sd(rows, 'int_min_end', decimals=0)} | {mean_sd(rows, 'rssi_mean')} |")
    return lines


def baseline_tables(folder):
    provenance = json.loads((folder / "baseline.json").read_text())
    with open(folder / "summary.csv") as summary_file:
        rows = list(csv.DictReader(summary_file))
    by_mode = {mode: [row for row in rows if row["mode"] == mode] for mode in MODES}
    by_mode = {mode: trials for mode, trials in by_mode.items() if trials}
    return ([provenance_line(provenance), ""] + throughput_table(by_mode) + [""] + stage_table(by_mode) + [""]
            + losses_table(by_mode))


def soak_header(summary):
    return (f"Soak `{summary['label']}`: {summary['duration_s']} s from {summary['started']}; device boots seen "
            f"{summary['device_boots_seen']} (unexpected resets {summary['unexpected_resets']}, reasons "
            f"{summary['reset_reasons']}); frames received {summary['frames_received']} ({summary['rx_fps_mean']} "
            f"fps mean, {summary['rx_bytes_per_s_mean']} B/s); seq gaps {summary['seq_gaps']}, CRC errors "
            f"{summary['crc_errors']}, missing pairs {summary['missing_pairs']}, reconnects {summary['reconnects']}.")


def soak_row(window, elapsed_min):
    return (f"| {elapsed_min:.0f} | {window.get('temp_c', 0):.1f} | {window.get('mem.int_free')} | "
            f"{window.get('mem.int_min')} | {window.get('mem.ps_min')} | {window.get('fps_processed', 0):.2f} | "
            f"{window.get('fps_sent', 0):.2f} | {window.get('counters.tx_dropped')} | {window.get('rssi')} |")


def soak_tables(folder):
    summary = json.loads((folder / "summary.json").read_text())
    with open(folder / "metrics.jsonl") as metrics_file:
        windows = [json.loads(line) for line in metrics_file]
    lines = [soak_header(summary), "",
             "| Elapsed (min) | die temp (°C) | int_free (B) | int_min (B) | ps_min (B) | fps processed | fps sent | "
             "tx_dropped (cum.) | RSSI |", "|---|---|---|---|---|---|---|---|---|"]
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
    worst_send = max(w["stages.t_send.max"] for w in windows if w.get("stages.t_send.max") is not None)
    temps = [w["temp_c"] for w in windows if w.get("temp_c") is not None]
    windows_with_drops = sum(1 for w in windows if (w.get("dropped_in_window") or 0) > 0)
    return lines + ["", f"Over the soak: die temp min/max {min(temps):.1f}/{max(temps):.1f} °C; worst per-window "
                        f"t_send max {worst_send / 1e3:.0f} ms; windows with any drop {windows_with_drops} of "
                        f"{len(windows)}."]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--soak", type=Path)
    args = parser.parse_args()
    lines = baseline_tables(args.baseline)
    if args.soak:
        lines += ["", "### Soak", ""] + soak_tables(args.soak)
    print("\n".join(lines))
    return 0

