# GMT people-flow

**A small camera that finds people, and a PC program that shows where they were and where they moved.**

This project was made for the General Magic (Thailand) high-school engineering research camp. A tiny computer board with a camera looks at a scene, uses artificial intelligence (AI) **on the board itself** to find the people in each picture, and sends the results over Wi-Fi to a laptop. The laptop shows the pictures with boxes around the people, draws two kinds of heatmaps, and saves every measurement to files.

Your job at the camp is to **measure this system like an engineer**:
- Find out how fast it really is, and which parts take the time.
- Make a guess (a *hypothesis*) about what limits it.
- Test your guess with careful experiments.
- Explain your evidence.

The system was built to be **measured and explained**. It has not been tuned to be fast, and nothing in it is broken on purpose.

New words are explained in the **[glossary](docs/glossary.md)**.

---

## How it works

```text
   THE BOARD (XIAO ESP32S3 Sense)                             YOUR LAPTOP
   ──────────────────────────────                             ───────────
   Core 1 – vision task, for every frame:                     viewer.py
     1. take a picture (JPEG, 320 × 240 pixels)                 • shows the picture and the boxes
     2. decode the JPEG into pixels                             • shows two heatmaps
     3. run the AI model to find people  → boxes + scores       • shows the board's measurements
     4. add the people to the presence heatmap                  • saves everything to a run folder
     5. put the finished frame in a short queue
                                                     Wi-Fi
   Core 0 – net task:                                ═════►
     sends frames, boxes, heatmap and measurements
     receives commands from the laptop               ◄═════
```

- **The board has two cores** (two "brains"). One runs the camera and the AI; the other runs the network. They pass finished frames to each other through a short **queue**. If the queue is full, the oldest frame is thrown away and counted, so the AI never has to wait for the Wi-Fi.
- **Everything is timed.** For every frame, the board measures how long each step took, in microseconds:
  - waiting for the camera;
  - decoding the JPEG;
  - preparing the image for the AI;
  - running the AI;
  - cleaning up the AI's answer;
  - sending the frame.

  Once per second it sends a summary of all these timings to the laptop.
- **Two different heatmaps:**
  - **Presence**, counted on the board, shows where people *were*.
  - **Movement**, worked out on the laptop, shows where people *moved*.

  They measure different things, so they can look different.
- **Five modes** let you switch parts of the system on and off, so you can find out what each part costs:

| Mode | What the board does |
|---|---|
| `camera_only` | only takes pictures |
| `inference_only` | runs the AI again and again on one saved photo (no camera) |
| `detect_no_stream` | camera + AI, but sends only measurements |
| `metadata_stream` | camera + AI, sends the boxes but no pictures |
| `full_stream` | everything, including the pictures (the default) |

---

## Getting started

### What you need
- The board (Seeed XIAO ESP32S3 Sense) with its **OV3660 camera**, the **Wi-Fi antenna** attached, and a **heatsink** on the chip.
- A USB-C cable that carries data (some cables only charge), plugged straight into the laptop or into a powered USB hub.
- A laptop with **Python 3.11 or newer**, on a **2.4 GHz** Wi-Fi network. The board cannot use 5 GHz Wi-Fi.

### Step 1 – Install the laptop programs (only once)
Open a terminal in this project folder and run:
```bash
python -m venv pc/.venv
pc/.venv/Scripts/pip install -r pc/requirements.txt
pc/.venv/Scripts/python -m pytest pc/tests -q
```
On a Mac or Linux laptop, write `pc/.venv/bin/` instead of `pc/.venv/Scripts/`.
- The first line makes a private Python folder for this project.
- The second installs the libraries the project needs.
- The third runs the automatic tests; the last line should say `34 passed`.

### Step 2 – Try it without a board
```bash
pc/.venv/Scripts/python pc/main.py viewer --replay samples/synthetic_run
```
You will see three coloured boxes walking across a grey picture labelled SYNTHETIC. This is made-up data, but it goes through exactly the same program as real data. Press **q** to close.

### Step 3 – Connect to a real board
Your mentor flashes the board (copies the firmware onto it) and tells you its **IP address**. Plug the board in, wait about one minute while it checks itself, then run:
```bash
pc/.venv/Scripts/python pc/main.py viewer --host <board-ip>
```

| Key | What it does |
|---|---|
| `o` | switch the heatmap: none → presence → movement |
| `m` | switch to the next mode |
| `r` | clear both heatmaps |
| `g` | ask the board for its presence heatmap now |
| `s` | start/stop saving camera pictures (off by default) |
| `c` | disconnect / reconnect |
| `q` or `Esc` | quit |

Every session is saved in a new **run folder**, `runs/<date_time>_<board>/`. The measurements are in `metrics.csv`, which you can open in Excel or Google Sheets. The file `stream.gmtp` lets you replay the session later:
```bash
pc/.venv/Scripts/python pc/main.py viewer --replay runs/<folder>
```
Camera pictures are saved **only** while you have pressed `s`.

### Step 4 – Run a fair, repeatable experiment
Ask your mentor to set up the reference scene first, so every team measures the same thing. Then run:
```bash
pc/.venv/Scripts/python pc/main.py baseline --host <board-ip> --room-temp-c 26.5 \
    --scene static_silhouettes --heatsink yes --repeats 3 --seconds 60
```
Replace 26.5 with the room temperature you measured.
- **What it does:** the script lets the board warm up for 2 minutes, then runs each of the five modes 3 times, for 60 seconds each. It takes about 25 minutes in total.
- **What it saves:** every setting is recorded in `baseline.json`, and one line of results per run in `summary.csv`.
- **Making tables:** `pc/main.py report` turns that folder into tables.

What every measurement means, the settings you can change, and a page for your notebook are in **[docs/experiments.md](docs/experiments.md)**. The step-by-step guide for students is **[docs/student_quickstart.md](docs/student_quickstart.md)**.

---

## Important limits and rules

**What the heatmaps cannot tell you.** Both heatmaps are measured in the *picture*, not on the floor, and they come from an AI that sometimes misses people:
- The camera's angle stretches distances, so they are not a floor plan.
- A person standing still adds to "presence" in every frame, so one person can look like a crowd.
- When the AI misses a person for a frame, their movement path breaks.
- If the frame rate changes, the raw counts change too. Compare per frame or per second instead.
- A tracklet (a short movement path) is not a person, so you cannot count people with it.

**Privacy.** The camera can see people:
- Saving pictures is **off** until you press `s`.
- Only turn it on when everyone in view has agreed.
- The measurement files never contain pictures.

The project does **not** recognise faces or identify people, and it does not follow anyone over time.

**Safety.** The chip gets hot: 60–74 °C was measured without a heatsink. Don't touch the chip or the heatsink while the board runs. Don't put the board in a closed plastic box.

---

## For mentors: building and flashing

1. **Build the firmware** (needs ESP-IDF v5.5.3; see [setup_windows.md](docs/setup_windows.md) or [setup_linux.md](docs/setup_linux.md)). In `firmware/`:
   ```bash
   idf.py build
   idf.py merge-bin -o gmt_people_flow_merged.bin
   ```
   Build from a committed tree. The firmware reports its git version, and a `-dirty` version fails the board check on purpose.
2. **Set the Wi-Fi:** copy `firmware/main/config/wifi_secrets.example.h` to `wifi_secrets.h` (not stored in git) and fill in a 2.4 GHz network.
   - Without it, each board makes its own network `GMT-PF-xxxx` (password `gmtcamp2026`, board at 192.168.4.1).
   - Wi-Fi settings can also be changed later from the laptop.
3. **Flash and check every connected board:**
   ```bash
   pc/tools/flash_all.ps1
   ```
   On Linux the equivalent is `pc/tools/flash_all.sh`. Each board prints **PASS** or **FAIL** with its MAC address, camera ID (it must be `0x3660`), memory sizes, firmware version and IP address, and a line is added to `boards.csv`.

Boards that won't flash, a wrong Wi-Fi password saved on a board, resets and camera errors: see [flashing_and_recovery.md](docs/flashing_and_recovery.md). How to run sessions: [mentor_notes.md](docs/mentor_notes.md).

---

## What is in this folder

```text
gmt-people-flow/
├── firmware/                 the program on the board (C++)
│   └── main/
│       ├── main.cpp          start-up: the only file at the top
│       ├── config/           all the settings (config.h is a good first file to read)
│       ├── vision/           camera, AI, presence heatmap
│       ├── network/          Wi-Fi, connection to the laptop, message format, commands
│       ├── telemetry/        measurements, warning messages, temperature
│       ├── utils/            small helpers
│       └── assets/           the test photo for the self-test
├── pc/                       the programs on the laptop (Python)
│   ├── main.py               the only starting point: python pc/main.py <command>
│   ├── ui/                   the viewer window
│   ├── analysis/             matching pictures with boxes, heatmaps, movement paths
│   ├── connection/           reading the board's messages, comparing clocks
│   ├── recording/            saving sessions, replaying them, the synthetic sample
│   ├── tools/                checks and experiments (baseline, soak, verify-board, ...)
│   ├── utils/                small helpers
│   └── tests/                34 automatic tests that run without a board
├── samples/synthetic_run/    a made-up session for trying the viewer without a board
└── docs/                     documentation (see below)
```

### `firmware/main/` – the program on the board
| Folder | Files | What they do |
|---|---|---|
| (top) | `main.cpp` | What happens at start-up; starts the two tasks |
| `config/` | `config.h` | **All the settings in one place**, such as picture size, JPEG quality and Wi-Fi |
| | `settings.h/.cpp` | The settings in use right now, changed from the laptop and saved |
| `vision/` | `vision.cpp` | The vision task: take a picture → decode → run the AI → heatmap → queue |
| | `camera.cpp`, `grid.cpp`, `bundle.h` | The camera, the presence heatmap, the "bundle" that carries a frame |
| `network/` | `net.cpp` | The net task: send everything to the laptop, receive commands |
| | `wifi.cpp`, `protocol.cpp`, `commands.cpp` | The Wi-Fi connection, the message format, the commands from the laptop |
| `telemetry/` | `metrics.cpp` | Collects the timings and makes the once-per-second summary |
| | `events.cpp`, `thermal.cpp` | Warning messages, temperature checks |
| `utils/` | `device.cpp` | Reset reason, MAC address, a memory helper |

Every `.cpp` file has a `.h` file next to it that lists what it offers to the other files.

### `pc/` – the programs on the laptop
Every program starts from `pc/main.py`. Run `python pc/main.py` on its own to see the list of commands:

| Command | What it does | Code |
|---|---|---|
| `viewer` | The window you look at (live, or `--replay` a saved session) | `ui/viewer.py` |
| `replay` | Play a saved session again and print its counters | `recording/replay.py` |
| `make-synthetic` | Write the made-up sample session | `recording/synthetic.py` |
| `check` | Connect, change settings (`--set`), check that everything arrives | `tools/check.py` |
| `baseline` | A repeatable experiment: every mode, several repeats, all conditions saved | `tools/baseline.py` |
| `report` | Tables from a baseline folder | `tools/report.py` |
| `soak` | A long test (an hour) to check the board stays stable | `tools/soak.py` |
| `verify-board` | Flash and check boards (mentors); `tools/flash_all.ps1` / `.sh` do every board | `tools/verify_board.py` |
| `serial-log` | Save the board's start-up messages over USB | `tools/serial_log.py` |

The commands share code in `analysis/` (matching pictures with boxes, counting losses, heatmaps, movement paths), `connection/` (the message format, comparing clocks), `recording/logger.py` (writing the run folder) and `utils/`.

### `docs/` – documentation
| Document | Who it is for |
|---|---|
| [glossary.md](docs/glossary.md) | Everyone: what the words mean |
| [student_quickstart.md](docs/student_quickstart.md) | Students: step-by-step guide |
| [experiments.md](docs/experiments.md) | Students: what each measurement means, settings, notebook page |
| [setup_windows.md](docs/setup_windows.md), [setup_linux.md](docs/setup_linux.md) | Installing the tools and building the firmware |
| [flashing_and_recovery.md](docs/flashing_and_recovery.md), [mentor_notes.md](docs/mentor_notes.md) | Mentors |
| [protocol.md](docs/protocol.md), [architecture.md](docs/architecture.md) | Anyone who wants the technical details |
| [performance_report.md](docs/performance_report.md), [test_report.md](docs/test_report.md) | Mentors and GMT: measurements and what has been checked |
| [versions.md](docs/versions.md), [research_findings.md](docs/research_findings.md), [risks.md](docs/risks.md) | Background: software versions, sources, known risks |

### How the code is written
- **Names explain themselves.** Settings and functions have names that say what they are, such as `BUNDLE_POOL_SIZE` or `copy_jpeg_and_release_frame()`.
- **Explanations live in docstrings.** There are no comments mixed into the code; the explanations are in the short description at the top of each file and above each function.
- **Message names never change.** The names of counters and settings are part of the message format, so saved logs keep working.

## Project status
| Stage | State |
|---|---|
| Choose the tools, test the hardware | Done. A 1-hour test without a heatsink passed (no resets, memory stable, highest 71.9 °C). **The test with a heatsink still waits for a heatsink.** |
| Board program | Done, tested on the real board |
| Laptop programs and tests | Done, 34 tests pass, tested with replay and with the real board |
| Official baseline measurements | Tools and documents are ready. **The measurements wait for a heatsink and the reference scene.** Early measurements are in [performance_report.md](docs/performance_report.md). |

Every result in [test_report.md](docs/test_report.md) is labelled honestly: tested on the real board, only built, only tested in simulation, not tested, or blocked.
