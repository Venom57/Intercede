// Bark detector + ultrasonic deterrent for ESP32-S3.
//
// Data flow:
//   I2S mic --(10 ms chunks)--> audioTask --+--> slices (250 ms) --> inferenceTask --> TriggerPolicy
//                                           |                                            |
//                                           +--> training clips --> loop() --> serial    +--> ultrasonic burst / LED / log
//
// Ships in DRY RUN mode: it detects and logs "would_fire" but never emits
// ultrasound until you type `arm` (or press BOOT). Watch the logs for a few
// days first and tune the threshold so it doesn't fire on the wrong sounds.
#include <Arduino.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>

#include <atomic>
#include <cmath>
#include <cstdlib>
#include <cstring>

#include "audio_input.h"
#include "config.h"
#include "console.h"
#include "detector.h"
#include "onset_detector.h"
#include "settings.h"
#include "settings_store.h"
#include "status_led.h"
#include "trigger_policy.h"
#include "ultrasonic.h"

namespace {

// ===================================================================== state

SemaphoreHandle_t g_settings_lock = nullptr;
bark::Settings g_settings;
std::atomic<uint32_t> g_settings_gen{0};

struct Stats {
  std::atomic<uint32_t> slices{0};
  std::atomic<uint32_t> overruns{0};
  std::atomic<uint32_t> barks{0};
  std::atomic<uint32_t> bursts{0};
  std::atomic<uint32_t> would_fire{0};
  std::atomic<uint32_t> suppressed{0};
  std::atomic<uint32_t> clips_sent{0};
  std::atomic<uint32_t> clips_dropped{0};
  std::atomic<uint32_t> detector_errors{0};
};
Stats g_stats;

std::atomic<bool> g_hw_error{false};
std::atomic<uint16_t> g_bursts_last_hour{0};
std::atomic<float> g_last_score{0.0f};
std::atomic<float> g_floor_db{-90.0f};
std::atomic<float> g_level_db{-90.0f};
std::atomic<float> g_peak_db{-90.0f};

std::atomic<bool> g_print_scores{false};
std::atomic<uint32_t> g_level_until_ms{0};
std::atomic<bool> g_capture_onsets{false};
std::atomic<uint32_t> g_rec_clips_left{0};

// ===================================================================== buffers

constexpr int kSlicePool = 3;
constexpr int kClipPool = 3;

struct Clip {
  int16_t* pcm;
  uint32_t samples;
  uint32_t t_ms;
  const char* reason;
};
Clip g_clips[kClipPool];

QueueHandle_t g_slice_free = nullptr;   // int16_t* buffers ready to fill
QueueHandle_t g_slice_ready = nullptr;  // filled slices for the detector
QueueHandle_t g_clip_free = nullptr;    // Clip* ready to fill
QueueHandle_t g_clip_ready = nullptr;   // finished clips for the serial port

int16_t g_preroll[kClipPrerollSamples];  // ring buffer, audio task only

uint64_t nowMs64() { return static_cast<uint64_t>(esp_timer_get_time() / 1000); }

int16_t* allocSamples(size_t count, bool prefer_psram) {
  const size_t bytes = count * sizeof(int16_t);
  void* p = nullptr;
  if (prefer_psram) p = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (p == nullptr) p = heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  return static_cast<int16_t*>(p);
}

bool allocateBuffers(size_t slice_samples) {
  g_slice_free = xQueueCreate(kSlicePool, sizeof(int16_t*));
  g_slice_ready = xQueueCreate(kSlicePool, sizeof(int16_t*));
  g_clip_free = xQueueCreate(kClipPool, sizeof(Clip*));
  g_clip_ready = xQueueCreate(kClipPool, sizeof(Clip*));
  if (!g_slice_free || !g_slice_ready || !g_clip_free || !g_clip_ready) return false;

  for (int i = 0; i < kSlicePool; ++i) {
    int16_t* buf = allocSamples(slice_samples, false);
    if (buf == nullptr) return false;
    xQueueSend(g_slice_free, &buf, 0);
  }
  for (int i = 0; i < kClipPool; ++i) {
    g_clips[i].pcm = allocSamples(kClipSamples, true);
    if (g_clips[i].pcm == nullptr) return false;
    Clip* c = &g_clips[i];
    xQueueSend(g_clip_free, &c, 0);
  }
  return true;
}

// ===================================================================== settings

bark::Settings getSettings() {
  xSemaphoreTake(g_settings_lock, portMAX_DELAY);
  const bark::Settings s = g_settings;
  xSemaphoreGive(g_settings_lock);
  return s;
}

void putSettings(const bark::Settings& s) {
  const bark::Settings clean = bark::sanitize(s);
  xSemaphoreTake(g_settings_lock, portMAX_DELAY);
  g_settings = clean;
  xSemaphoreGive(g_settings_lock);
  g_settings_gen.fetch_add(1);
  settingsSave(clean);
}

void refreshLedMode() {
  if (g_hw_error.load()) {
    ledSetMode(LedMode::kError);
  } else if (millis() < kMicSettleMs + 1000) {
    ledSetMode(LedMode::kBooting);  // noise floor still being learned
  } else if (g_capture_onsets.load() || g_rec_clips_left.load() > 0) {
    ledSetMode(LedMode::kCapture);
  } else {
    ledSetMode(getSettings().armed ? LedMode::kArmed : LedMode::kDryRun);
  }
}

// ===================================================================== audio task

void copyPreroll(int16_t* dst, size_t pos) {
  // pos is the oldest sample in the ring.
  const size_t first = kClipPrerollSamples - pos;
  memcpy(dst, g_preroll + pos, first * sizeof(int16_t));
  memcpy(dst + first, g_preroll, pos * sizeof(int16_t));
}

void audioTask(void*) {
  const size_t slice_len = detectorInfo().slice_samples;
  int16_t chunk[kChunkSamples];
  bark::OnsetDetector gate;  // finds sounds worth saving as training clips

  size_t pre_pos = 0;
  int16_t* slice = nullptr;
  size_t slice_fill = 0;
  Clip* clip = nullptr;
  const uint32_t settle_until = millis() + kMicSettleMs;

  for (;;) {
    const size_t got = audioRead(chunk, kChunkSamples);
    if (got == 0) {
      vTaskDelay(1);
      continue;
    }
    if (static_cast<int32_t>(millis() - settle_until) < 0) continue;

    // ---- level meter (console "level" command)
    double sum_sq = 0.0;
    int32_t peak = 0;
    for (size_t i = 0; i < got; ++i) {
      sum_sq += static_cast<double>(chunk[i]) * chunk[i];
      const int32_t a = std::abs(static_cast<int32_t>(chunk[i]));
      if (a > peak) peak = a;
    }
    g_level_db.store(static_cast<float>(10.0 * std::log10(sum_sq / got / (32768.0 * 32768.0) + 1e-12)));
    g_peak_db.store(static_cast<float>(20.0 * std::log10(peak / 32768.0 + 1e-9)));

    // ---- loud-sound gate
    const bool onset = gate.process(chunk, got);
    bark::SoundEvent ev;
    while (gate.popEvent(&ev)) {
    }
    g_floor_db.store(gate.floorDb());
    if (onset) ledFlash(LedFlash::kSound);

    // ---- training clips
    if (clip == nullptr) {
      const bool want_onset = onset && g_capture_onsets.load();
      const bool want_rec = !want_onset && g_rec_clips_left.load() > 0;
      if (want_onset || want_rec) {
        Clip* c = nullptr;
        if (xQueueReceive(g_clip_free, &c, 0) == pdTRUE) {
          clip = c;
          clip->t_ms = millis();
          clip->samples = 0;
          if (want_onset) {
            clip->reason = "onset";
            copyPreroll(clip->pcm, pre_pos);
            clip->samples = kClipPrerollSamples;
          } else {
            clip->reason = "rec";
            uint32_t left = g_rec_clips_left.load();
            while (left > 0 && !g_rec_clips_left.compare_exchange_weak(left, left - 1)) {
            }
          }
        } else {
          g_stats.clips_dropped.fetch_add(1);  // PC isn't keeping up
        }
      }
    }
    if (clip != nullptr) {
      const size_t room = kClipSamples - clip->samples;
      const size_t n = got < room ? got : room;
      memcpy(clip->pcm + clip->samples, chunk, n * sizeof(int16_t));
      clip->samples += n;
      if (clip->samples >= kClipSamples) {
        xQueueSend(g_clip_ready, &clip, 0);
        clip = nullptr;
      }
    }

    // ---- pre-roll ring (after the clip copy, so a new clip's pre-roll ends
    //      right before this chunk)
    for (size_t i = 0; i < got; ++i) {
      g_preroll[pre_pos] = chunk[i];
      pre_pos = (pre_pos + 1) % kClipPrerollSamples;
    }

    // ---- detector slices
    size_t off = 0;
    while (off < got) {
      if (slice == nullptr) {
        if (xQueueReceive(g_slice_free, &slice, 0) != pdTRUE) {
          slice = nullptr;
          g_stats.overruns.fetch_add(1);  // detector is falling behind; drop audio
          break;
        }
        slice_fill = 0;
      }
      const size_t n = (got - off) < (slice_len - slice_fill) ? (got - off) : (slice_len - slice_fill);
      memcpy(slice + slice_fill, chunk + off, n * sizeof(int16_t));
      slice_fill += n;
      off += n;
      if (slice_fill == slice_len) {
        xQueueSend(g_slice_ready, &slice, 0);
        slice = nullptr;
      }
    }
  }
}

// ===================================================================== inference task

void inferenceTask(void*) {
  bark::TriggerPolicy policy;
  bark::Settings s;
  uint32_t seen_gen = UINT32_MAX;
  bool label_error_logged = false;

  for (;;) {
    int16_t* slice = nullptr;
    xQueueReceive(g_slice_ready, &slice, portMAX_DELAY);
    float score = detectorRunSlice(slice);
    xQueueSend(g_slice_free, &slice, 0);

    const uint64_t now = nowMs64();
    g_stats.slices.fetch_add(1);

    const uint32_t gen = g_settings_gen.load();
    if (gen != seen_gen) {
      s = getSettings();
      policy.configure(bark::toPolicyConfig(s));
      seen_gen = gen;
    }

    if (score < 0.0f) {
      g_stats.detector_errors.fetch_add(1);
      if (score <= -2.0f && !label_error_logged) {
        logLine("E model has no class named '%s' - set BARK_LABEL in config.h", BARK_LABEL);
        label_error_logged = true;
      }
      continue;
    }
    g_last_score.store(score);
    if (g_print_scores.load()) {
      logLine("SCORE,%lu,%.3f", static_cast<unsigned long>(now), score);
    }

    // Ignore our own bridge/piezo clicks.
    const uint32_t now32 = static_cast<uint32_t>(now);
    if (ultrasonicActive() || now32 - ultrasonicLastEndMs() < kSelfNoiseGuardMs) score = 0.0f;

    const bark::Decision d = policy.update(now, score);
    const uint16_t last_hour = policy.triggersInLastHour(now);
    g_bursts_last_hour.store(last_hour);

    switch (d) {
      case bark::Decision::kIdle:
        break;
      case bark::Decision::kBark:
        g_stats.barks.fetch_add(1);
        ledFlash(LedFlash::kBark);
        logEvent("bark", score, last_hour);
        break;
      case bark::Decision::kTrigger:
        g_stats.barks.fetch_add(1);
        if (s.armed) {
          const BurstParams p{s.burst_ms, s.freq_lo_hz, s.freq_hi_hz, s.sweep_period_ms};
          if (ultrasonicBurst(p)) {
            g_stats.bursts.fetch_add(1);
            ledFlash(LedFlash::kFiring, s.burst_ms);
            logEvent("burst", score, last_hour);
          } else {
            logEvent("burst_busy", score, last_hour);
          }
        } else {
          g_stats.would_fire.fetch_add(1);
          ledFlash(LedFlash::kWouldFire);
          logEvent("would_fire", score, last_hour);
        }
        break;
      case bark::Decision::kSuppressedCooldown:
        g_stats.suppressed.fetch_add(1);
        ledFlash(LedFlash::kSuppressed);
        logEvent("cooldown", score, last_hour);
        break;
      case bark::Decision::kSuppressedHourlyCap:
        g_stats.suppressed.fetch_add(1);
        ledFlash(LedFlash::kSuppressed);
        logEvent("hourly_cap", score, last_hour);
        break;
    }
  }
}

// ===================================================================== console

void printSettings(const bark::Settings& s) {
  logLine("I mode: %s", s.armed ? "ARMED (ultrasonic bursts enabled)" : "DRY RUN (logs would_fire, never emits)");
  logLine("I threshold=%.2f k=%u n=%u cooldown=%lums maxhour=%u burst=%ums freq=%lu-%luHz sweep=%ums",
          s.threshold, s.k, s.n, static_cast<unsigned long>(s.cooldown_ms), s.max_per_hour, s.burst_ms,
          static_cast<unsigned long>(s.freq_lo_hz), static_cast<unsigned long>(s.freq_hi_hz),
          s.sweep_period_ms);
}

void printStatus() {
  const DetectorInfo& info = detectorInfo();
  logLine("I detector: %s, %u-sample slices", info.name, static_cast<unsigned>(info.slice_samples));
  printSettings(getSettings());
  logLine("I bursts in the last hour: %u", g_bursts_last_hour.load());
  logLine("I slices=%lu barks=%lu bursts=%lu would_fire=%lu suppressed=%lu overruns=%lu errors=%lu",
          static_cast<unsigned long>(g_stats.slices.load()), static_cast<unsigned long>(g_stats.barks.load()),
          static_cast<unsigned long>(g_stats.bursts.load()), static_cast<unsigned long>(g_stats.would_fire.load()),
          static_cast<unsigned long>(g_stats.suppressed.load()), static_cast<unsigned long>(g_stats.overruns.load()),
          static_cast<unsigned long>(g_stats.detector_errors.load()));
  logLine("I clips sent=%lu dropped=%lu, capture=%s, rec clips left=%lu",
          static_cast<unsigned long>(g_stats.clips_sent.load()),
          static_cast<unsigned long>(g_stats.clips_dropped.load()), g_capture_onsets.load() ? "on" : "off",
          static_cast<unsigned long>(g_rec_clips_left.load()));
  logLine("I noise floor %.1f dBFS, last score %.2f", g_floor_db.load(), g_last_score.load());
  logLine("I free heap: internal %u, PSRAM %u",
          static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
          static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
}

void printStatLine() {
  logLine("STAT,%lu,floor=%.1f,slices=%lu,barks=%lu,bursts=%lu,would_fire=%lu,suppressed=%lu,overruns=%lu",
          static_cast<unsigned long>(millis()), g_floor_db.load(),
          static_cast<unsigned long>(g_stats.slices.load()), static_cast<unsigned long>(g_stats.barks.load()),
          static_cast<unsigned long>(g_stats.bursts.load()), static_cast<unsigned long>(g_stats.would_fire.load()),
          static_cast<unsigned long>(g_stats.suppressed.load()),
          static_cast<unsigned long>(g_stats.overruns.load()));
}

void printHelp() {
  logLine("I commands:");
  logLine("I   status                 settings, counters, noise floor");
  logLine("I   arm | disarm           enable bursts | dry run (log only)");
  logLine("I   set threshold 0.8      bark score needed per slice (0.05-0.99)");
  logLine("I   set k 2 / set n 4      k positive slices within the last n (250 ms each)");
  logLine("I   set cooldown 5000      ms between bursts");
  logLine("I   set maxhour 12         bursts allowed per rolling hour");
  logLine("I   set burst 800          burst length, ms (100-2000)");
  logLine("I   set freq 23000 27000   sweep range, Hz (22000-40000)");
  logLine("I   set sweep 120          sweep period, ms");
  logLine("I   defaults               restore default settings (also disarms)");
  logLine("I   test [ms]              fire one burst now, even in dry run");
  logLine("I   capture on|off         send loud sounds to the PC as 1 s clips");
  logLine("I   rec <seconds>          send continuous audio to the PC as 1 s clips");
  logLine("I   scores on|off          print every slice score");
  logLine("I   level [seconds]        print mic level (check gain/clipping)");
  logLine("I   reboot");
}

void testBurst(uint16_t ms) {
  const bark::Settings s = getSettings();
  const BurstParams p{ms, s.freq_lo_hz, s.freq_hi_hz, s.sweep_period_ms};
  if (ultrasonicBurst(p)) {
    ledFlash(LedFlash::kFiring, ms);
    logLine("I test burst %u ms, %lu-%lu Hz", ms, static_cast<unsigned long>(s.freq_lo_hz),
            static_cast<unsigned long>(s.freq_hi_hz));
  } else {
    logLine("E burst already running (or ultrasonic driver failed to start)");
  }
}

void setArmed(bool armed) {
  bark::Settings s = getSettings();
  s.armed = armed;
  putSettings(s);
  logLine("I %s", armed ? "ARMED: bursts enabled" : "DISARMED: dry run, logging only");
}

void handleCommand(char* line) {
  char* argv[4] = {nullptr, nullptr, nullptr, nullptr};
  int argc = 0;
  for (char* tok = strtok(line, " \t"); tok != nullptr && argc < 4; tok = strtok(nullptr, " \t")) {
    argv[argc++] = tok;
  }
  if (argc == 0) return;
  const char* cmd = argv[0];

  if (strcmp(cmd, "help") == 0 || strcmp(cmd, "?") == 0) {
    printHelp();
  } else if (strcmp(cmd, "status") == 0) {
    printStatus();
  } else if (strcmp(cmd, "arm") == 0) {
    setArmed(true);
  } else if (strcmp(cmd, "disarm") == 0) {
    setArmed(false);
  } else if (strcmp(cmd, "set") == 0) {
    bark::Settings s = getSettings();
    switch (bark::applySetting(s, argv[1], argv[2], argv[3])) {
      case bark::SetResult::kOk:
        putSettings(s);
        printSettings(getSettings());
        break;
      case bark::SetResult::kUnknownKey:
        logLine("E unknown setting '%s' (type help)", argv[1] ? argv[1] : "");
        break;
      case bark::SetResult::kBadValue:
        logLine("E bad value for '%s'", argv[1]);
        break;
    }
  } else if (strcmp(cmd, "defaults") == 0) {
    putSettings(bark::Settings());
    printSettings(getSettings());
  } else if (strcmp(cmd, "test") == 0) {
    long ms = argv[1] ? strtol(argv[1], nullptr, 10) : 500;
    if (ms < bark::limits::kBurstMinMs) ms = bark::limits::kBurstMinMs;
    if (ms > bark::limits::kBurstMaxMs) ms = bark::limits::kBurstMaxMs;
    testBurst(static_cast<uint16_t>(ms));
  } else if (strcmp(cmd, "capture") == 0) {
    const bool on = argv[1] && strcmp(argv[1], "on") == 0;
    g_capture_onsets.store(on);
    if (!on) g_rec_clips_left.store(0);
    logLine("I capture %s", on ? "on" : "off");
  } else if (strcmp(cmd, "rec") == 0) {
    long secs = argv[1] ? strtol(argv[1], nullptr, 10) : 0;
    if (secs < 1 || secs > 3600) {
      logLine("E usage: rec <seconds 1-3600>");
    } else {
      g_rec_clips_left.store(static_cast<uint32_t>(secs));
      logLine("I recording %ld s", secs);
    }
  } else if (strcmp(cmd, "scores") == 0) {
    g_print_scores.store(argv[1] && strcmp(argv[1], "on") == 0);
  } else if (strcmp(cmd, "level") == 0) {
    long secs = argv[1] ? strtol(argv[1], nullptr, 10) : 10;
    if (secs < 1) secs = 1;
    if (secs > 600) secs = 600;
    g_level_until_ms.store(millis() + static_cast<uint32_t>(secs) * 1000);
  } else if (strcmp(cmd, "reboot") == 0) {
    logLine("I rebooting");
    delay(100);
    ESP.restart();
  } else {
    logLine("E unknown command '%s' (type help)", cmd);
  }
}

// BOOT button: short press toggles armed / dry run, hold 2 s for a test burst.
void handleButton() {
  static bool was_down = false;
  static uint32_t down_at = 0;
  static bool long_done = false;

  const bool down = digitalRead(kPinButton) == LOW;
  const uint32_t now = millis();
  if (down && !was_down) {
    down_at = now;
    long_done = false;
  }
  if (down && !long_done && now - down_at >= 2000) {
    long_done = true;
    ledFlash(LedFlash::kButton);
    testBurst(500);
  }
  if (!down && was_down && !long_done && now - down_at >= 30) {
    ledFlash(LedFlash::kButton);
    setArmed(!getSettings().armed);
  }
  was_down = down;
}

void printLevel() {
  const float rms = g_level_db.load();
  const float peak = g_peak_db.load();
  char bar[41];
  int n = static_cast<int>((rms + 90.0f) / 90.0f * 40.0f);
  n = n < 0 ? 0 : (n > 40 ? 40 : n);
  memset(bar, '#', n);
  bar[n] = '\0';
  logLine("LEVEL rms=%6.1f peak=%6.1f floor=%6.1f dBFS |%-40s|%s", rms, peak, g_floor_db.load(), bar,
          peak > -1.0f ? " CLIPPING: raise kMicShift" : "");
}

}  // namespace

// ===================================================================== Arduino

void setup() {
  consoleBegin(kSerialBaud);
  pinMode(kPinButton, INPUT_PULLUP);
  ledBegin();
  ledSetMode(LedMode::kBooting);
  delay(200);

  g_settings_lock = xSemaphoreCreateMutex();
  g_settings = settingsLoad();

  logLine("I bark-deterrent starting");

  // Drive the bridge low first, before anything else can go wrong.
  if (!ultrasonicBegin()) {
    logLine("E ultrasonic driver failed to start (check pins in config.h)");
    g_hw_error.store(true);
  }
  if (!detectorBegin()) {
    logLine("E detector failed to start");
    g_hw_error.store(true);
  }
  if (!allocateBuffers(detectorInfo().slice_samples)) {
    logLine("E out of memory allocating audio buffers");
    g_hw_error.store(true);
  }
  if (!audioBegin()) {
    logLine("E I2S microphone failed to start (check wiring/pins)");
    g_hw_error.store(true);
  }

  if (!g_hw_error.load()) {
    const uint32_t inference_stack = detectorInfo().is_ml ? 32 * 1024 : 8 * 1024;
    xTaskCreatePinnedToCore(audioTask, "audio", 6 * 1024, nullptr, 10, nullptr, 0);
    xTaskCreatePinnedToCore(inferenceTask, "infer", inference_stack, nullptr, 5, nullptr, 1);
  }

  printStatus();
  if (!detectorInfo().is_ml) {
    logLine("I no Edge Impulse model found: using the placeholder detector (any short loud sound).");
    logLine("I collect clips with tools/capture_clips.py and train a model - see README.");
  }
  logLine("I type 'help' for commands. BOOT button: press = arm/disarm, hold 2 s = test burst.");
}

void loop() {
  static uint32_t last_stat = 0;
  static uint32_t last_level = 0;
  static uint32_t last_led = 0;
  char line[128];

  if (consoleReadLine(line, sizeof(line))) handleCommand(line);
  handleButton();

  Clip* c = nullptr;
  while (g_clip_ready != nullptr && xQueueReceive(g_clip_ready, &c, 0) == pdTRUE) {
    consoleSendClip(c->pcm, c->samples, c->t_ms, c->reason);
    g_stats.clips_sent.fetch_add(1);
    ledFlash(LedFlash::kClip);
    xQueueSend(g_clip_free, &c, 0);
  }

  const uint32_t now = millis();
  if (static_cast<int32_t>(g_level_until_ms.load() - now) > 0 && now - last_level >= 200) {
    last_level = now;
    printLevel();
  }
  if (now - last_stat >= kStatusIntervalMs) {
    last_stat = now;
    printStatLine();
  }
  if (now - last_led >= 100) {
    last_led = now;
    refreshLedMode();
  }
  delay(5);
}
