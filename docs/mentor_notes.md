# Mentor notes

These notes help you **run** the camp. They deliberately contain no answers about what limits the system or how to speed it up: students should find, argue and test that themselves. If you know an answer, ask a question that leads to a measurement instead of giving it away.

`performance_report.md` has preliminary numbers for preparing yourself. Don't share it with students before they have measured the system.

## Before the camp
- [ ] One heatsink per board. The heatsink soak (`pc/main.py soak --label heatsink`) is still to be run and recorded in `test_report.md` (BLOCKED at the time of writing: no heatsink).
- [ ] Build one image from a **committed** tree and flash every board with `pc/tools/flash_all.ps1`. Keep `boards.csv`, and stick the MAC on each board. Set aside any board that fails, especially a sensor other than `0x3660`.
- [ ] A **2.4 GHz** network at the venue that both boards and laptops can join, plus a fallback plan: each board can also run its own network (`flashing_and_recovery.md`).
- [ ] Reference scenes ready (`experiments.md` §7): silhouettes or a photo at a fixed distance, a looping walking video with clear rights, tripods or clamps, fixed lighting.
- [ ] Room thermometer (students must record the room temperature).
- [ ] Run `pc/main.py baseline` once on the reference scene and tag the commit `baseline-v1` (handoff Gate 4), so every team starts from the same recorded baseline.
- [ ] Laptops: Python venv installed, and `pytest pc/tests` passes (34 tests).

## Each session
1. Boards on direct USB ports or powered hubs; antennas on; heatsinks on.
2. Run `pc/main.py verify-board --all` at the start of the day. It's quick insurance against a swapped or reflashed board.
3. Let boards stream at least 2 minutes before any measurement.
4. Remind teams: recording images stays off unless everyone in view has agreed.

## Things students will likely run into (phenomena, not explanations)
Point at the data and ask; don't explain.
- Device-processed fps, sent fps, received fps and display fps differ. *Which one did you report, and why that one?*
- `tx_dropped` and `frame_id` gaps appear in bursts, and the RSSI changes. *What else changed at the same moment?*
- The die temperature climbs for the first minutes. *Would a measurement taken in minute 1 equal one taken in minute 10? How would you check?*
- The first inference after boot is slower than the rest. *Should it be in your average?*
- Capture → display is shown with ± uncertainty. *Where does that uncertainty come from, and can you shrink it?*
- The presence and movement heatmaps disagree. *They measure different things: what does each actually count?*
- Changing `jpeg_quality`, `frame_size` or `infer_every_n` changes several numbers at once. *Which of those changes did you expect?*
- `metrics_verbose` makes the device send more. *Does measuring change what you measure?*

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

Write any new failure into `docs/risks.md` with its symptoms.
