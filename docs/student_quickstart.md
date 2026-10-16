# Student quickstart

In this camp you work like a real engineer. You get a working system: a small camera board that finds people using AI and sends what it sees to your laptop. You will **measure** how it behaves, make a **guess about what limits it**, and **test** your guess with careful experiments.

Before you start, your mentor gives you:
- the board, with a label showing its **MAC address** (its unique hardware number);
- the board's **IP address** (its address on the Wi-Fi network, for example `192.168.1.248`).

Words you don't know are explained in the [glossary](glossary.md).

---

## Safety and privacy first

- **The chip gets hot enough to hurt.** Don't touch it while the board is running. Don't put the board inside a closed plastic box.
- **The camera can see people.** Saving pictures is **off** at the start. Only turn it on (key `s`) when everyone the camera can see has said yes. The measurement files never contain pictures.

---

## 1. Set up your laptop (only once)

Open a terminal in the project folder and type:
```bash
python -m venv pc\.venv
pc\.venv\Scripts\pip install -r pc\requirements.txt
```

Now test it without the board:
```bash
pc\.venv\Scripts\python pc\main.py viewer --replay samples\synthetic_run
```
You should see coloured boxes moving across a grey picture with the word SYNTHETIC. This is made-up data that goes through the same program as real data. Press `q` to close the window.

---

## 2. Connect the board

1. Check that the **Wi-Fi antenna** (the small flat sticker on a wire) is plugged into the board.
2. Plug the board into a USB port on your laptop, or into a powered USB hub.
3. **Wait about one minute.** Every time the board starts, it tests its camera and its AI first.
4. Make sure your laptop is on the **same Wi-Fi network** as the board. Ask your mentor which one.
5. Start the viewer, writing your board's IP address instead of `<board-ip>`:
   ```bash
   pc\.venv\Scripts\python pc\main.py viewer --host <board-ip>
   ```

If the viewer says `connect failed`, check the IP address and the Wi-Fi network, then ask your mentor.

---

## 3. Read the screen

**On the left: the camera picture.**
- **Green boxes** are people the AI found. The number above each box is the **score**: how sure the AI is, from 0 to 1.
- **Yellow dots** are the middle of the bottom edge of each box, roughly where the person's feet are. The heatmaps use this point.
- **`frame_id`** in the corner is the number of this picture. It goes up by one for every picture the board processes.

**On the right: the numbers.**
- **DEVICE** is what the board says about itself, updated once per second:
  - how many frames per second it **processed** and **sent**;
  - how long the AI took (**t_infer**);
  - how long one whole frame took (**t_proc**);
  - its **temperature**;
  - how many frames it had to **drop**.
- **HOST** is what your laptop measured:
  - frames per second received;
  - how long drawing took;
  - any lost data;
  - **capture → display**: the time from the camera taking a picture to your screen showing it. It is always shown with a "±" value, which tells you how accurate the number is (see the glossary entry *clock offset*).
- **EVENTS** are messages from the board, such as "mode changed" or warnings.

**Under the picture:** what the current heatmap means, with its **units**, and its **limits**. Read these before you draw any conclusion from a heatmap.

| Key | What it does |
|---|---|
| `o` | switch the heatmap: none → presence → movement |
| `m` | switch to the next **mode** (see [experiments.md](experiments.md), section 4) |
| `r` | clear both heatmaps |
| `g` | ask the board for its presence heatmap now |
| `s` | start/stop saving camera pictures |
| `c` | disconnect / reconnect |
| `q` | quit |

---

## 4. Where your data goes

Every time you run the viewer, it makes a new **run folder**: `runs\<date_time>_<board>\`. The most useful files are:
- `metrics.csv`: the board's measurements, one row per second. Open it in Excel or Google Sheets.
- `host_stats.csv`: your laptop's measurements, one row per second.
- `events.jsonl`: the board's messages.

The full list is in [experiments.md](experiments.md), section 6.

You can watch a saved session again at any time:
```bash
pc\.venv\Scripts\python pc\main.py viewer --replay runs\<folder>
```

---

## 5. Run a fair, repeatable experiment

A fair experiment is one where **only the thing you are testing changes**. Everything else stays the same: the scene, the lighting, the board, the warm-up. So:

1. Ask your mentor to set up the **reference scene** (printed people shapes or a video on a screen, with fixed lighting, and the camera on a stand).
2. Measure the room temperature with a thermometer.
3. Run the measurement script. Write your own numbers and names instead of the examples:
   ```bash
   pc\.venv\Scripts\python pc\main.py baseline --host <board-ip> --room-temp-c 26.5 --scene reference_scene --heatsink no --repeats 3 --seconds 60
   ```

The script warms the board up for 2 minutes, then measures each of the 5 modes 3 times, for 60 seconds each. It takes about **25 minutes**, so don't touch the setup while it runs.

The results are saved in `baselines\...\`:
- `summary.csv`: one line of results per run;
- `baseline.json`: every setting that was used.

---

## 6. Work like an engineer

1. **Measure the unchanged system first.** This is your **baseline**. Write it down (there is a notebook page in [experiments.md](experiments.md), section 8).
2. **Write a hypothesis before you change anything.** It should be a clear guess that a measurement could prove wrong. For example: "If I do X, then measurement Y will go up, because Z."
3. **Change one thing at a time.** Repeat each test at least **3 times**. Compare your results with a baseline measured under the same conditions.
4. **Look for another explanation.** Could something else have caused the change? Design a test that tells the two explanations apart.
5. **Report honestly.** Give numbers **with units**, say how much they changed between repeats (the spread), and say what you could *not* measure.
