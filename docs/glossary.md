# Glossary

Words you will meet in this project, explained in plain language. They are grouped by topic. When a document or the code uses a word you don't know, look here first.

---

## The board

**Microcontroller**
A small computer on a single chip. It has no screen or keyboard, and it runs one program (the firmware) as soon as it gets power. Our microcontroller is the **ESP32-S3**.

**XIAO ESP32S3 Sense**
The small board we use. It holds the ESP32-S3 chip, a camera, a Wi-Fi antenna connector and a USB-C port.

**Core**
One "brain" inside a chip that runs instructions. The ESP32-S3 has **two cores**, so it can run two jobs at the same time. In our firmware, core 1 handles the camera and the AI, and core 0 handles the network.

**Firmware**
The program that runs on the board. It lives in the `firmware/` folder.

**Flashing**
Copying the firmware onto the board's memory over USB. A mentor usually does this for you.

**Flash memory**
Memory that keeps its contents when the power is off, like a USB stick. The board has 8 MB. The firmware and the AI model are stored here.

**RAM, internal RAM, PSRAM**
Working memory, which is fast and is erased when the power goes off. The chip has a small, very fast **internal RAM** (about half a megabyte) and an extra 8 MB of **PSRAM**, which is bigger but a little slower. Large things, like images, live in PSRAM.

**Camera sensor, OV3660, PID**
The chip inside the camera that turns light into pixels. Ours is an **OV3660**. Every sensor model reports an ID number called its **PID**; an OV3660 says `0x3660`. Some boards were shipped with a different sensor (OV2640), which is why we check the PID.

**Boot, reset**
**Boot** is the board starting up. A **reset** restarts it, like turning it off and on again. The board records *why* it last restarted, for example `POWERON`, `USB` or `BROWNOUT`.

**Brown-out**
When the power supply voltage drops too low for a moment, for example because of a weak USB port or a bad cable, and the chip resets itself to stay safe.

**Die temperature**
The temperature of the silicon chip itself, measured by a sensor built into the chip. It is hotter than the air around the board.

**Heatsink**
A piece of metal attached to a hot chip that spreads the heat into the air, so the chip stays cooler.

**Serial log**
Text messages the board prints over the USB cable while it runs. It is useful for seeing what happened at boot.

---

## Images

**Pixel**
One dot of an image. Each pixel has a colour.

**Frame**
One still picture from the camera. A video is many frames shown quickly one after another.

**Resolution, QVGA**
The number of pixels in a frame, written as width × height. **QVGA** is 320 × 240 pixels, which is our default. Other sizes you may meet: QQVGA (160 × 120), HVGA (480 × 320) and VGA (640 × 480).

**JPEG, JPEG quality**
A way to store an image in fewer bytes by throwing away detail the eye barely notices (this is called *compression*). The camera sends JPEG images. In our settings, a **lower** `jpeg_quality` number means *better* quality and *bigger* files.

**Decode**
Turning a JPEG back into a full grid of pixels that a program can work with.

**RGB**
Storing each pixel as three numbers: how much Red, Green and Blue it has.

---

## The AI part

**Neural network, model**
A program that has learned to recognise patterns from many examples, instead of being written rule by rule. Our model, `pedestrian_detect`, was trained by Espressif to find people (pedestrians) in images.

**Inference**
Running the model on one image to get an answer. It is called "inference" because the model *infers* (works out) where the people are. In our timing, this is **t_infer**.

**Detection, bounding box**
When the model finds a person, it reports a **bounding box**: a rectangle around the person, given by its corners (x1, y1) at the top left and (x2, y2) at the bottom right, in pixels.

**Score, threshold**
Each box comes with a **score** between 0 and 1: how sure the model is that the box contains a person. Boxes below the **threshold** (`score_thr`, 0.7 by default) are thrown away.

**Preprocess**
Preparing an image for the model. Our model needs exactly 224 × 224 pixels, stored in a compact number format. In our timing, this is **t_pre**.

**Postprocess, NMS**
Turning the model's raw output into a clean list of boxes. One step, **NMS** (Non-Maximum Suppression), removes duplicate boxes around the same person and keeps the best one. In our timing, this is **t_post**.

**Missed detection (false negative)**
A person the model did not find, for example because they were too small, too far away, partly hidden or seen from an unusual angle.

---

## Software inside the board

**Task**
A job the firmware runs on its own, like an app on a phone. We have two main tasks: the **vision task** (camera and AI) and the **net task** (network).

**FreeRTOS**
The small operating system inside the firmware. It decides which task runs when.

**Buffer, pool**
A **buffer** is a piece of memory that holds data, for example one frame. A **pool** is a fixed set of buffers created once at start-up and reused again and again, so the firmware never runs out of memory while it is running.

**Queue, drop-oldest**
A **queue** is a waiting line between two tasks: the vision task puts finished frames in, and the net task takes them out to send. Our queue holds only 2 frames. If it is full when a new frame is ready, the **oldest** waiting frame is thrown away (**drop-oldest**) and counted as `tx_dropped`. This way the AI never has to stop and wait for the network.

**Watchdog**
A safety timer. If a task stops responding for too long, the watchdog notices and reports it, or resets the board.

**Settings, NVS**
The firmware has settings (called *knobs* in our docs), such as the mode or the frame size. They can be changed from the PC while the board runs. **NVS** is a small area of flash memory where settings are saved so they survive a reboot.

**Counter**
A number that goes up by one every time something happens, for example `cap_ok` (frames captured) or `tx_dropped` (frames dropped).

---

## Network

**Wi-Fi 2.4 GHz**
The radio band the board uses for Wi-Fi. The board **cannot** use 5 GHz Wi-Fi.

**IP address**
A device's address on the network, for example `192.168.1.248`. You give the viewer this address so it can find the board.

**Station mode, SoftAP**
In **station mode**, the board joins an existing Wi-Fi network, as a phone does. In **SoftAP** mode, the board creates its *own* small Wi-Fi network (named `GMT-PF-xxxx`) and you connect to it.

**TCP, port**
**TCP** is a way of sending data over a network that delivers every byte, in order, or reports an error. A **port** is a numbered "door" on a device; our board listens on port **5000**.

**Protocol, record**
A **protocol** is an agreed format, so two programs understand each other. Ours is called **GMTP**. It sends data in **records**: small packages, each with a label saying what it contains (a frame, a list of boxes, measurements and so on).

**Checksum, CRC**
A number calculated from the data and sent with it. The receiver calculates it again; if the two numbers differ, the data was damaged on the way. It is like the check digit on an ID card number. **CRC** is the kind of checksum we use.

**Sequence number (seq)**
Every record gets the next number: 0, 1, 2, … If the PC sees a number skipped, a record is missing (`seq_gaps`).

**RSSI**
**Received Signal Strength Indicator**: how strong the Wi-Fi signal is, in dBm. It is always a negative number: −50 is strong, −70 is weak, and −85 is very weak.

**Bandwidth, bytes per second**
How much data is sent each second. 1 kB/s is about 1,000 bytes per second.

**Backoff**
Waiting longer and longer between retries (1 s, 2 s, 4 s, …) when something keeps failing, such as connecting to Wi-Fi, so the device doesn't keep hammering the network.

---

## Measuring

**Microsecond (µs), millisecond (ms)**
1 ms is one thousandth of a second. 1 µs is one millionth of a second, so 1 ms = 1,000 µs. The board measures time in µs.

**Timestamp**
The time at which something happened, according to a clock. The board and the PC each have their **own** clock.

**Latency**
How long **one** thing takes from start to finish, for example how long one frame takes to process (`t_proc`). Lower is faster.

**Throughput**
How **many** things are finished per second, for example frames per second (fps). Higher is more. Latency and throughput are not the same thing: a shop can take 10 minutes to make one pizza but still finish more than 6 pizzas an hour if it works on several at once.

**fps (frames per second)**
How many frames are handled each second. Our docs distinguish *processed* fps (the board finished the AI), *sent* fps (the board sent the frame), *received* fps (the PC got it) and *display* fps (the PC drew it).

**Window**
A short time period that measurements are grouped into. The board reports its measurements once per **1-second window**.

**n, min, mean, max**
For a group of measurements: **n** is how many there are, **min** the smallest, **max** the largest, and **mean** the average (add them all up, then divide by n).

**Median, p95 (95th percentile)**
Sort all measurements from fastest to slowest. The **median** is the one in the middle. **p95** is the value that 95 % of the measurements are at or below: only the slowest 5 % are above it. p95 shows how bad the slow cases get, which an average can hide.

**Standard deviation (SD), spread**
How much the measurements differ from their mean. A small SD means the results are consistent; a large SD means they jump around. "7.33 ± 0.03" means a mean of 7.33 with an SD of 0.03.

**Repeat, trial**
A **trial** is one measurement run, for example 60 seconds in one mode. A **repeat** is doing the same trial again, to see whether you get the same result.

**Warm-up**
Running the system for a while before you start measuring, so that it settles, for example so the temperature stops rising.

**Baseline**
The measurements of the **unchanged** system under fixed, recorded conditions. You compare every experiment against the baseline.

**Hypothesis**
A guess about the cause of something, written so that a measurement can show whether it is wrong. "If I move the board closer to the Wi-Fi router, the RSSI will get stronger" is a hypothesis; "the Wi-Fi is bad" is not, because no measurement can prove it wrong.

**Variables: independent, dependent, controlled**
The **independent** variable is the one thing you change on purpose. The **dependent** variable is what you measure to see the effect. **Controlled** variables are everything you keep the same.

**Clock offset, round trip time (RTT), uncertainty**
The board's clock and the PC's clock show different times, like two watches that were never set to match. To compare them, the PC sends a **ping** with its own time; the board answers with *its* time. The **round trip time** is how long the question and answer took together. The board's answer was made somewhere inside that time, so the comparison is only accurate to within **± half the round trip time**. That ± value is the **uncertainty**, and it is why every end-to-end time in this project is shown with a ± value, for example "≈ 150 ms ± 5 ms".

---

## Heatmaps

**Grid, cell**
The image is divided into 20 × 15 equal rectangles called **cells**. A heatmap gives each cell a number and shows it as a colour (blue = low, red = high).

**Bottom-center point**
The middle of a box's bottom edge, which is roughly where the person's feet are. Both heatmaps use this point.

**Presence intensity**
Counted on the board: for every processed frame, +1 in the cell where each detected person's feet are. It shows where people *were*. A person standing still adds +1 in every frame.

**Tracklet**
A short link the PC makes between a box in one frame and the nearest box in the next frame, guessing that it is the same person moving. Tracklets have no names or identities, and they disappear after a few frames without a match. A tracklet is **not** a count of people.

**Movement map**
Computed on the PC: each time a tracklet moves far enough, every cell it crosses gets +1. It shows where people *moved*.

**Normalize**
Dividing a count by something, such as the number of frames or the number of seconds, so that runs of different lengths can be compared fairly.

---

## Files and data

**Log**
A file that records what happened, line by line.

**CSV**
A table saved as plain text, one row per line with values separated by commas. It opens in any spreadsheet program, such as Excel or Google Sheets.

**JSON, JSONL**
**JSON** is a text format for structured data, like `{"temp_c": 61.9, "mode": "full_stream"}`. **JSONL** is a file with one JSON object per line.

**Run folder**
The folder the viewer writes for each session: `runs/<date_time>_<device>/`.

**Replay**
Playing a saved session (or the synthetic sample) through the viewer again, without the board.

**Synthetic data**
Data made by a program instead of recorded from the real camera. `samples/synthetic_run/` contains three moving coloured boxes, so you can try the viewer without hardware.
