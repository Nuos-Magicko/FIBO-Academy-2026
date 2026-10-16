# Synthetic run (generated, not recorded)

Made by `python pc/main.py make-synthetic ../samples/synthetic_run`. 70 frames at 7 fps, 320x240, three box-shaped
walkers. On purpose: frame_id 40 is missing (a device drop), frame 30 has no DETECTIONS (a missing pair),
walker 0 is not detected on frame 20 (a missed detection). Replay: `python pc/main.py replay ../samples/synthetic_run`
or `python pc/main.py viewer --replay ../samples/synthetic_run`.
