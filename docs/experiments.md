# Experiments guide

This guide explains **what every measurement means**, the **modes** and **settings** you can use in your experiments, and gives you a **notebook page** for each trial.

This system is a starting point to investigate. It is not a puzzle with one right answer, and there is no target speed to reach. Unknown words are in the [glossary](glossary.md).

---

## 1. What the board times, step by step

For every frame, the board writes down the time before and after each step, using its own clock in **microseconds (µs)**; 1,000 µs = 1 ms. Each step has a name starting with `t_`:

| Name | What happens during this time | Starts when… | Ends when… |
|---|---|---|---|
| `t_wait_frame` | The board waits for the camera to deliver a picture | it asks the camera for a picture | the picture arrives |
| `t_copy` | The JPEG picture is copied into a sending buffer (`full_stream` mode only) | the copy starts | the copy ends |
| `t_decode` | The JPEG is turned back into pixels (RGB) | decoding starts | the pixels are ready |
| `t_pre` | The picture is prepared for the AI: shrunk to 224 × 224 pixels and converted to the model's number format | preparation starts | it ends |
| `t_infer` | The AI model itself runs | the model starts | the model finishes |
| `t_post` | The AI's raw answer is turned into a clean list of boxes, with duplicates removed | clean-up starts | the box list is ready |
| `t_grid` | The presence heatmap is updated | the update starts | it ends |
| `t_enqueue` | The finished frame is handed to the network task, including any waiting | the hand-off starts | the frame is in the queue |
| `t_proc` | **The whole job for one frame on the board** | the picture arrived from the camera | the frame is in the queue |
| `t_send` | The network task sends one frame over Wi-Fi | sending starts | the last byte is handed to the network |

Things to know:
- The board gives the picture's memory back to the camera as soon as it has finished with it: right after `t_copy` in `full_stream`, or right after `t_decode` in the other modes.
- Where exactly each timer starts and stops is in the code: `firmware/main/vision/vision.cpp` and `firmware/main/network/net.cpp`.

---

## 2. Speed has two meanings: latency and throughput

- **Latency** is how long **one** frame takes, for example `t_proc`.
- **Throughput** is how **many** frames are finished each second (fps).

They are related, but they are **not the same**. Knowing one does not tell you the other.

This project counts frames per second at four places:

| Name | Where it is counted | Meaning |
|---|---|---|
| processed fps (`fps_processed`) | on the board | frames the board finished (AI done) |
| sent fps (`fps_sent`) | on the board | frames the board finished sending |
| capture fps | on the board | pictures taken by the camera |
| received fps / display fps | on your laptop (`host_stats.csv`) | frames your laptop received / drew on screen |

If these numbers are different, something happened in between. For example, when the board's sending queue is full, it throws away the oldest frame. Your laptop then sees a jump in `frame_id`, counted as **frame_id gaps**.

---

## 3. Measuring the time from camera to screen

The board's clock and your laptop's clock **do not show the same time**. So you cannot simply subtract the board's "picture taken" time from the laptop's "picture shown" time.

To compare the two clocks, the viewer does this every 2 seconds:
1. The laptop sends a message (a "ping") containing its own time.
2. The board answers at once with *its* time.
3. The laptop measures how long the round trip took (the **RTT**).

The board's answer was made somewhere during that round trip, so the clock comparison is only accurate to within **± half the RTT**. The viewer keeps the fastest round trip, because it gives the smallest error.

That is why "capture → display" is always shown with a ± value, for example **≈ 150 ms ± 5 ms** (made-up numbers). Always write down the ± value with the number. A camera-to-screen time without its ± value is not a valid result.

---

## 4. The five modes

Each mode switches parts of the system on or off, so you can measure what each part does:

| Mode | What the board does | What it sends over Wi-Fi |
|---|---|---|
| `camera_only` | only takes pictures and throws them away | measurements and messages |
| `inference_only` | runs the AI again and again on one saved photo (640 × 480 pixels); the camera is off | the boxes, measurements, messages |
| `detect_no_stream` | camera + AI + presence heatmap | measurements and messages only |
| `metadata_stream` | camera + AI + presence heatmap | the boxes and the heatmap, but **no pictures** |
| `full_stream` (default) | camera + AI + heatmap + a copy of each picture | everything, **including the pictures** |

To switch modes, press `m` in the viewer. The current mode is written in every measurement row, so your files always show which mode was running.

---

## 5. Settings you can change

You can change these settings while the board is running. They are tools for your experiments, **not** recommended values. Change one at a time, and write down what you changed.

| Setting | What it changes | Allowed values |
|---|---|---|
| `frame_size` | picture size | QQVGA (160×120), QVGA (320×240, default), HVGA (480×320), VGA (640×480) |
| `jpeg_quality` | picture quality. **Lower number = better quality, bigger file.** | 4 to 63 (default 12) |
| `infer_every_n` | run the AI only on every Nth picture | 1 to 1000 (default 1 = every picture) |
| `score_thr` | minimum score for a box to count | 0.01 to 1.0 (default 0.7) |
| `xclk_hz` | the camera's clock speed | 5,000,000 to 24,000,000 (default 20,000,000) |
| `metrics_interval_ms` | how often the board sends its summary | 100 to 60,000 ms (default 1000) |
| `metrics_verbose` | also send the timing of **every** frame | true / false |
| `grid_every_n` | how often the presence heatmap is sent | 1 to 65535 frames (default 50) |
| `warmup_frames` | pictures thrown away after the camera starts | 0 to 1000 (default 10) |

To change a setting, send it from the laptop with `--set name=value`, for example:
```bash
pc\.venv\Scripts\python pc\main.py check <board-ip> --seconds 3 --set jpeg_quality=20
```
You can give several at once, such as `--set jpeg_quality=20 --set infer_every_n=2`. Check the printed `events` to see whether it worked: `cmd_ok` means the board accepted it, and `cmd_error` tells you what was wrong.
The change lasts until the board restarts. Ask your mentor if you want to keep a setting permanently.

---

## 6. Your data files

**The board's summary (once per second).** For each timed step, the summary gives:
- `n`: how many frames were measured;
- `min`: the fastest;
- `mean`: the average;
- `max`: the slowest;
- `p95`: 95 % of the frames were this fast or faster.

It also includes counters (totals since the board started), free memory (`int_min` and `ps_min` = the lowest it has ever been), the chip temperature, the Wi-Fi signal strength (RSSI), and how long the board has been running.

**Your run folder,** `runs\<date_time>_<board>\`:

| File | Contents |
|---|---|
| `hello.json` | the board's details and all its settings |
| `metrics.csv` | the board's summary, one row per second (open in a spreadsheet) |
| `host_stats.csv` | your laptop's measurements, one row per second |
| `detections.jsonl` | every box the AI found, frame by frame |
| `events.jsonl` | the board's messages |
| `grid_snapshots.csv` | the presence heatmap, each time it was sent |
| `stream.gmtp` | everything received, for replaying the session later |
| `frames\` | camera pictures, **only** if you turned saving on with `s` |

**A baseline folder,** `baselines\<date>_<board>_<scene>\`, made by `pc/main.py baseline`:
- `baseline.json`: every setting and condition;
- `summary.csv`: one result line per run;
- one run folder per run.

---

## 7. Getting results you can trust

- **Warm up first.** Let the board run for at least **2 minutes** before you measure. The script does this for you, and also waits 15 seconds after every mode change.
- **Write down the conditions** of every trial:
  - the room temperature;
  - whether the heatsink is on;
  - the scene;
  - the board's MAC address;
  - the chip temperature at the start and end. The script records this.
- **Use the reference scene.** Use printed people shapes or a photo on a screen at a fixed distance, or a looping video of people walking. Keep the lighting fixed and put the camera on a stand.
- **Point the camera sensibly.** The AI was trained on pictures taken at about eye level. It works best with the camera about 2–2.5 m high, looking down at an angle, with people filling at least a quarter of the picture height.
- **Note the Wi-Fi conditions:** the signal strength (RSSI) and the time of day.

---

## 8. Notebook page for one trial

Copy this for every trial. The scripts fill in many of the values for you: see `baseline.json` and `summary.csv`.

```
Date and time:          Team:                 Trial number:
My question / hypothesis:
Board MAC:              Camera ID (sensor PID):    Firmware version:
Software versions (from hello.json):
What I changed from the default settings (setting = value, or "nothing"):
Mode:                   Repeat number:        Length (s):       Warm-up (s):
Scene:                  Lighting:             Camera height and angle:
Room temperature (°C):  Heatsink (yes/no):    Power (laptop port / hub):   Wi-Fi signal RSSI (dBm):
Chip temperature at start / end / highest (°C):
Results (always with units):
   frames per second processed / sent / received:
   t_decode / t_pre / t_infer / t_post / t_proc / t_send (mean and p95, in ms):
   frames dropped / frame_id gaps / missing pairs / sequence gaps:
Run folder name:
What surprised me:
Another possible explanation, and a test that could tell the two apart:
```
