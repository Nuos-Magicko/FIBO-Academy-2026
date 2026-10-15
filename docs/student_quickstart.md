# Student quickstart

You will run an existing embedded-AI system **unmodified**, measure it, and explain what limits it, the way working engineers do. Your mentor has flashed your board and given you its **IP address** and **MAC label**.

## Safety and privacy first
- The board gets hot (60–70 °C measured with no heatsink). Don't touch the chip or the heatsink while it runs. Don't put it inside a closed plastic box.
- The camera sees people. **Recording images is off by default.** Turn it on (key **s**) only when everyone in view has agreed. Telemetry logs never contain images.

## 1. Set up the PC (once)
Windows, in the project folder:
```bash
python -m venv pc\.venv
pc\.venv\Scripts\pip install -r pc\requirements.txt
```
On Linux/macOS, use `python3` and `pc/.venv/bin/pip`. Test it without the board:
```bash
pc\.venv\Scripts\python pc\viewer.py --replay samples\synthetic_run
```
You should see moving coloured boxes labelled SYNTHETIC. Close the window with **q**.

## 2. Connect the board
1. Plug the board into a USB port on the laptop (or a powered hub). Check that the Wi-Fi antenna is attached.
2. Wait about 1 minute; it runs self-tests at every boot.
3. Your laptop must be on the same Wi-Fi network as the board (ask your mentor which one).
4. Start the viewer:
   ```bash
   pc\.venv\Scripts\python pc\viewer.py --host <board-ip>
   ```

## 3. Read the screen
- **Left:** the camera image, green boxes (detected people, with scores), yellow dots (each box's bottom-center, the point the heatmaps use), and the `frame_id`.
- **Right, DEVICE:** what the board reports about itself every second: processed/sent fps, inference time, temperature, dropped frames.
- **Right, HOST:** what your laptop measures: received fps, drawing time, gaps, and the capture→display estimate **with its ± uncertainty**.
- **Bottom:** what the current overlay means, with units, and its limitations. Read them before drawing conclusions from a heatmap.

| Key | Does |
|---|---|
| **o** | overlay: none → presence (device) → movement (PC) |
| **m** | next experiment mode (see `experiments.md` §4) |
| **r** | reset both heatmaps |
| **g** | ask the board for its grid now |
| **s** | recording of images on/off |
| **c** | disconnect/reconnect |
| **q** | quit |

## 4. Where your data goes
Every session writes a folder `runs/<date_time>_<device>/`; `experiments.md` §6 lists the files. `metrics.csv` and `host_stats.csv` open in any spreadsheet. Replay a session later with:
```bash
pc\.venv\Scripts\python pc\viewer.py --replay runs\<folder>
```

## 5. Run a repeatable trial
Ask your mentor to set up the reference scene first. Then, with nothing else changed:
```bash
pc\.venv\Scripts\python scripts\collect_baseline.py --host <board-ip> --room-temp-c <measured> --scene <scene-name> --heatsink yes --repeats 3 --seconds 60
```
This takes about 25 minutes: a 2-minute warm-up, then 5 modes × 3 repeats. Results land in `baselines/…/summary.csv`, with every setting recorded in `baseline.json`.

## 6. Work like an engineer
1. Measure the **unmodified** baseline first, and write it down (template: `experiments.md` §8).
2. Write a hypothesis about what limits the system *before* changing anything.
3. Change **one** thing at a time, repeat at least 3 times, and compare with the baseline taken under the same conditions.
4. Look for a competing explanation, and design a test that would tell the two apart.
5. Report numbers with units, the spread across repeats, and what you could *not* measure.
