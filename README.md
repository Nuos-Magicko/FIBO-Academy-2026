# GMT people-flow reference app (XIAO ESP32S3 Sense + OV3660)

A working, measurable, deliberately **unoptimized** embedded-AI system for the GMT high-school engineering research camp. The board captures JPEG frames, runs on-device pedestrian detection (ESP-DL `pedestrian_detect`), keeps a presence grid, and streams frames, boxes, the grid and telemetry over Wi-Fi to a Python viewer and logger on a PC. The PC also computes an approximate movement map.

Students measure the system and explain what they see. Nothing here is tuned toward a target, and nothing is broken on purpose.

## What you need
- Seeed XIAO ESP32S3 Sense with the **OV3660** camera, the U.FL Wi-Fi antenna attached, and a heatsink.
- A USB-C data cable to a direct laptop port or a powered hub (expect 400+ mA).
- A PC with Python 3.11+ (3.12.4 verified), on a **2.4 GHz** network the board can join. The board cannot use 5 GHz.
- For building firmware only: ESP-IDF **v5.5.3**.

## Quick start (board already flashed)
```bash
python -m venv pc/.venv
pc/.venv/Scripts/pip install -r pc/requirements.txt        # Linux/macOS: pc/.venv/bin/pip
pc/.venv/Scripts/python pc/viewer.py --host <board-ip>      # IP: see verify_board.py output or the serial log
```
No board yet? `pc/.venv/Scripts/python pc/viewer.py --replay samples/synthetic_run` plays a generated sample through the same pipeline.

## Repository
| Path | What |
|---|---|
| `firmware/` | ESP-IDF project: camera, detector, presence grid, Wi-Fi, GMTP protocol, five experiment modes |
| `pc/` | `viewer.py` (live/replay view + logging), `replay.py`, `test_client.py`, `tracker.py`, `heatmap.py`, `logger.py`, `pipeline.py`, `protocol.py`, `clock_sync.py`, `tests/` |
| `scripts/` | `verify_board.py` / `flash_all.*` (flash + check every board), `collect_baseline.py`, `soak.py`, `serial_capture.py` |
| `samples/synthetic_run/` | Generated (not recorded) stream for replay without hardware |
| `docs/` | See below |

## Documentation
| Document | For |
|---|---|
| [student_quickstart.md](docs/student_quickstart.md) | Students: run the baseline and log a trial |
| [experiments.md](docs/experiments.md) | Measurement definitions, modes, knobs, log template |
| [setup_windows.md](docs/setup_windows.md) / [setup_linux.md](docs/setup_linux.md) | Installing the toolchain and building |
| [flashing_and_recovery.md](docs/flashing_and_recovery.md) | Mentors: flashing many boards, fixing stuck ones |
| [mentor_notes.md](docs/mentor_notes.md) | Mentors: running sessions (no prescribed answers) |
| [protocol.md](docs/protocol.md) | Wire format |
| [architecture.md](docs/architecture.md) | How it fits together |
| [test_report.md](docs/test_report.md) | What has been verified, and how |
| [versions.md](docs/versions.md), [research_findings.md](docs/research_findings.md), [risks.md](docs/risks.md) | Pinned stack, sources, open risks |

## Status (2026-10-15, unit #1)
| Gate | State |
|---|---|
| 0 Environment and research | Done |
| 1 Hardware spike + 1 h soak | Passed without a heatsink (0 resets, memory bounded, max 71.9 °C). **Heatsink soak blocked: no heatsink yet** |
| 2 Device application | Done, verified on hardware |
| 3 PC viewer / logger / replay / tests | Done: 27 tests pass; viewer verified on replay and on the live device |
| 4 Baseline freeze | Docs and scripts done. **Baseline data waits for a heatsink and the reference scene**; tag `baseline-v1` after that |

Every result in `docs/test_report.md` is labelled VERIFIED ON HARDWARE, BUILD-VERIFIED ONLY, SIMULATED-UNIT-TESTED, NOT TESTED or BLOCKED.

## Limits of the heatmaps
Presence intensity (device) and approximate movement (PC) are image-space measures from a detector, sampled at a few frames per second. They are not a floor plan and not a people count:
- perspective skews them;
- stationary people dominate presence;
- missed detections break paths;
- a change in frame rate invalidates raw count comparisons;
- a tracklet is not a person.

## Privacy
The camera sees people. Recording of camera images is **off by default**: telemetry logs never contain images, and JPEGs are saved only while recording is switched on. Record people only with their consent. No face recognition, identification, re-identification or persistent tracking is implemented. Tracklets are ephemeral and use position only.
