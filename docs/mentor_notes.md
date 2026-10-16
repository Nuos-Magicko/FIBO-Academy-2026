# Mentor notes

These notes help you **run** the camp. They deliberately contain no answers about what limits the system or how to speed it up: students should find, argue and test that themselves. If you know an answer, ask a question that leads to a measurement instead of giving it away.

## Before the camp
- [ ] Build one image from a **committed** tree and flash the boards one at a time with `python pc/main.py verify-board --port COMx --flash`. Keep `boards.csv`, and stick the MAC on each board. Set aside any board that fails, especially a sensor other than `0x3660`.
- [ ] A **2.4 GHz** network at the venue that both boards and laptops can join, plus a fallback plan: each board can also run its own network (`flashing_and_recovery.md`).
- [ ] Reference scenes ready (`experiments.md` §7): silhouettes or a photo at a fixed distance, a looping walking video with clear rights, tripods or clamps, fixed lighting.
- [ ] Room thermometer (students must record the room temperature).
- [ ] Students record their own baseline on the reference scene before changing anything (README, Step 4).
- [ ] Laptops: Python venv installed, and `pytest pc/tests` passes (36 tests).

## Each session
1. Boards on direct USB ports or powered hubs; antennas on.
2. Run `pc/main.py verify-board --port COMx` for each board at the start of the day. It's quick insurance against a swapped or reflashed board.
3. Let boards stream at least 2 minutes before any measurement.
4. Remind teams: recording images stays off unless everyone in view has agreed.

## Good questions to ask any team
- What exactly does this number measure: where does the timer start and stop (`experiments.md` §1)?
- How many repeats? What is the spread? What else was different between runs?
- What is your competing explanation, and which experiment would rule one out?
- Is this a latency claim or a throughput claim?
- Would your conclusion survive a different scene, board or time of day?

## Ground rules to enforce
- The baseline stays **unmodified** until a team has recorded it.
- One change at a time, written down before the run.
- No end-to-end latency claims without the clock uncertainty.
- Heatmaps are image-space and sampled. No "foot traffic" or "people count" claims.
- No face recognition, identification or tracking of individuals. This is out of scope by design.

## When something breaks
Use `flashing_and_recovery.md`. The fixes that come up most:
- BOOT button + replug for a board that won't flash;
- `erase_region 0x9000 0x6000` to clear bad saved settings;
- a powered hub or shorter cable for brown-outs;
- `xclk_hz 10000000` for camera timeouts.

Write down any new failure with its symptoms and send it to GMT.
