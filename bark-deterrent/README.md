# bark-deterrent

ESP32-S3 firmware that listens for barking with an I2S MEMS mic, scores it
with a small ML model, and answers a bark episode with a short ultrasonic
burst. The onboard NeoPixel shows what it's hearing and deciding.

Built for the **Lonely Binary ESP32-S3 (N16R8)** with an **INMP441** mic and
a **DRV8871** H-bridge driving a piezo transducer. All pins live in
[`include/config.h`](include/config.h).

> **Starter project.** The decision logic is unit tested on a PC, but the
> firmware hasn't been run on real hardware yet. Expect to adjust pins, mic
> gain and thresholds. It ships in **dry-run mode**: it logs and flashes
> "would fire" but never emits ultrasound until you arm it.

## How it works

```
INMP441 ──I2S──▶ audio task ──250 ms slices──▶ detector ──score──▶ trigger policy ──▶ burst / LED / log
 (16 kHz)          │                          (Edge Impulse          (k of n slices,
                   └──loud sounds──▶ 1 s WAV    model, or a           cooldown,
                       clips to your PC         placeholder)          hourly cap)
```

- **Detector:** an Edge Impulse model if you've put one in `lib/`, otherwise
  a *placeholder* that reacts to any short, loud sound in the bark band
  (400 Hz–4 kHz). The placeholder is for testing the hardware. It can't tell
  a bark from a clap or a shout.
- **Trigger policy:** fires only when *k* of the last *n* slices score above
  the threshold (default 2 of 4, so about 1 s). It then waits out a cooldown
  (default 5 s) and never fires more than the hourly cap (default 12).
- **Ultrasonic:** MCPWM drives the H-bridge with two opposite square waves
  sweeping 23–27 kHz. The output is hard-limited to 22–40 kHz and 2 s per
  burst, whatever the settings say. Both bridge inputs are held low whenever
  it isn't bursting.

## Hardware

| Part | Notes |
|---|---|
| Lonely Binary ESP32-S3 N16R8 | 16 MB flash, 8 MB PSRAM, WS2812 on GPIO48 |
| INMP441 I2S microphone breakout | ICS-43434 also works (same wiring) |
| DRV8871 H-bridge breakout | 6.5–45 V, handles 25 kHz PWM |
| Ultrasonic piezo, ~25 kHz | A 25 kHz piezo transducer or a piezo horn tweeter. **Not** a regular speaker (they stop at ~20 kHz), and not the 40 kHz distance-sensor type (too quiet) |
| 22 Ω resistor | In series with the piezo, to tame the current spikes at each edge |
| 12 V supply | For the bridge; power the ESP32 from USB or a 5 V buck converter |

### Wiring

| From | To |
|---|---|
| INMP441 VDD | 3V3 |
| INMP441 GND | GND |
| INMP441 L/R | GND (left channel) |
| INMP441 SCK | GPIO4 |
| INMP441 WS | GPIO5 |
| INMP441 SD | GPIO6 |
| DRV8871 IN1 | GPIO12 |
| DRV8871 IN2 | GPIO13 |
| DRV8871 VM / GND | 12 V supply + / − |
| DRV8871 GND | ESP32 GND (**grounds must be shared**) |
| DRV8871 OUT1 → 22 Ω → piezo → OUT2 | |

Keep the mic away from the piezo and the bridge wiring. Point the
transducer at where the dog actually barks: ultrasound fades fast and is
blocked by fences and walls.

## Build and flash

Install [PlatformIO](https://platformio.org/install) (VS Code extension or
`pip install platformio`), then from this folder:

```sh
pio run -t upload
pio device monitor        # 921600 baud; type `help`
```

`platformio.ini` uses the [pioarduino](https://github.com/pioarduino/platform-espressif32)
platform (Arduino-ESP32 core 3.x), because the code uses the ESP-IDF 5.x I2S
and MCPWM drivers.

## Status LED

| Colour | Meaning |
|---|---|
| Blue pulse | Booting / learning the background noise level |
| Teal breathing | Listening, **dry run** (never emits) |
| Green breathing | Listening, **armed** |
| Purple | Capture/record mode (sending clips to the PC) |
| Fast red blink | Hardware error, see the serial log |
| Dim white blip | Loud sound heard |
| Yellow | Bark-positive slice |
| Magenta | Dry run: it **would** have fired |
| Red, solid | Ultrasonic burst in progress |
| Orange | Bark episode, but blocked by cooldown or hourly cap |
| Cyan blip | Training clip sent |

**BOOT button:** press to toggle armed / dry run; hold 2 s for a 500 ms test burst.

## Serial console

```
status                 settings, counters, noise floor
arm | disarm           enable bursts | dry run (log only)
set threshold 0.8      bark score needed per slice (0.05-0.99)
set k 2 / set n 4      k positive slices within the last n (250 ms each)
set cooldown 5000      ms between bursts
set maxhour 12         bursts allowed per rolling hour
set burst 800          burst length, ms (100-2000)
set freq 23000 27000   sweep range, Hz (22000-40000)
set sweep 120          sweep period, ms
defaults               restore default settings (also disarms)
test [ms]              fire one burst now, even in dry run
capture on|off         send loud sounds to the PC as 1 s clips
rec <seconds>          send continuous audio to the PC as 1 s clips
scores on|off          print every slice score
level [seconds]        print mic level (check gain/clipping)
reboot
```

Settings are saved to flash and survive a reboot. Each event is one CSV-style
line, so you can log the serial port to a file and graph it later:

```
EVT,<uptime_ms>,<bark|would_fire|burst|cooldown|hourly_cap>,<score>,<bursts_last_hour>
STAT,<uptime_ms>,floor=-61.8,slices=...,barks=...,bursts=...,would_fire=...
```

## Getting it working, step by step

### 1. Bring-up

1. Flash it and open the monitor. You should see `I bark-deterrent starting`
   and the LED should go from blue to teal.
2. Run `level 20` and talk or clap. RMS should sit well below 0 dBFS and the
   peak should never hit `CLIPPING`. If it does, raise `kMicShift` in
   `config.h`. If even loud sounds barely move the bar, lower it.
3. Run `test 1000` with the piezo connected. You won't hear it. Check it with
   a cheap **bat detector**, or an oscilloscope across the piezo.
4. Play a dog-barking video near the mic (or say "woof! woof!"). The
   placeholder detector should flash yellow, then magenta ("would fire").
   Claps are too short (under 60 ms) to count as bark-like.

### 2. Collect training data

```sh
pip install -r tools/requirements.txt
python tools/capture_clips.py /dev/ttyUSB0            # every loud sound -> dataset/unlabeled/*.wav
python tools/capture_clips.py /dev/ttyUSB0 --rec 600  # 10 min of background -> noise class
```

Leave the device **where it will be deployed**: matching the real mic and
location matters more than anything else. Listen to the clips and sort them
into `bark/` and `noise/` folders. Put voices, doors, birds, cars and TV in
`noise/` (or their own classes). Aim for at least a few hundred clips per
class. Public data helps fill gaps: UrbanSound8K's `dog_bark` class,
AudioSet's "Bark" label, ESC-50's `dog` class.

### 3. Train in Edge Impulse

1. Create a project named **`bark_detector`** at [edgeimpulse.com](https://edgeimpulse.com).
2. *Data acquisition:* upload the WAVs, labelled `bark` / `noise` (the
   firmware looks for a class called exactly `bark`; change `BARK_LABEL` in
   `config.h` otherwise).
3. *Impulse design:* time-series input at **16000 Hz**, **1000 ms window**,
   **Audio (MFE)** processing, **Classification** learning block.
4. Generate features, train, and check the confusion matrix on the test set.
   False positives on `noise` matter more than missed barks.
5. *Deployment:* **Arduino library**, quantized (int8), EON compiler on.

### 4. Deploy the model

Unzip the downloaded library into `lib/` (you should get
`lib/bark_detector_inferencing/`), then `pio run -t upload`. `status` should
now say `detector: edge-impulse`. Use `scores on` to watch live scores.

### 5. Dry run, then arm

Leave it in dry run for a few days with the serial output logged to a file.
Count the `would_fire` events against what actually happened. Raise
`threshold` or `k` if it fires on the wrong things. Once you're happy, `arm`.

## Tests

```sh
make -C test/host                          # C++ logic: policy, sound gate, sweep, settings
python -m unittest discover -s tools       # serial clip parser
```

## Layout

```
include/config.h          pins and compile-time constants
src/main.cpp              tasks, console commands, button
src/audio_input.*         INMP441 over I2S (std mode, 32-bit slots -> 16-bit PCM)
src/detector.*            Edge Impulse model or placeholder
src/ultrasonic.*          MCPWM complementary sweep -> H-bridge
src/status_led.*          NeoPixel feedback
src/console.*             serial logging + clip framing
src/settings_store.*      NVS persistence
lib/bark_logic/           board-independent logic (host-tested)
tools/capture_clips.py    saves streamed clips as WAV
test/host/                host unit tests (make)
```

## Things to keep in mind

- **Results vary.** Many dogs get used to ultrasonic deterrents, and deaf or
  older dogs won't react. It works best alongside reward-based training. Use
  the logs to see whether barking is actually going down.
- **It reaches every animal in range**, including your own cats, rabbits or
  rodents, and dogs that weren't barking.
- **People:** the output never goes below 22 kHz, but some young people hear
  close to that. Test with your household.
- The cooldown and hourly cap exist so a mistake can't turn into continuous
  output. Keep them.

## Troubleshooting

| Symptom | Check |
|---|---|
| No serial output | You're on the native USB port: uncomment `ARDUINO_USB_CDC_ON_BOOT` in `platformio.ini`, or use the UART port |
| LED never lights | Your board revision may use another pin: change `kPinStatusLed` (some S3 boards use GPIO38) |
| `level` shows about -90 dBFS and never moves | Mic wiring. L/R must go to GND, and SCK/WS/SD to the right pins |
| Placeholder fires on everything | Expected: train a model. Meanwhile, raise `k` |
| `overruns` keeps rising | The model is too slow for 250 ms slices. Use a smaller model or enable EON |
| `E model has no class named 'bark'` | Rename the class in Edge Impulse or change `BARK_LABEL` |
