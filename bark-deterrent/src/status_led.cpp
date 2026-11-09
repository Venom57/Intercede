#include "status_led.h"

#include <Arduino.h>

#include <atomic>
#include <cmath>

#include "config.h"

namespace {

struct Rgb {
  uint8_t r, g, b;
};

// Kept dim on purpose: a WS2812 at full power is blinding and runs warm.
constexpr Rgb kBlue{0, 0, 40};
constexpr Rgb kTeal{0, 25, 25};
constexpr Rgb kGreen{0, 40, 0};
constexpr Rgb kPurple{25, 0, 35};
constexpr Rgb kRed{60, 0, 0};
constexpr Rgb kMagenta{50, 0, 40};
constexpr Rgb kOrange{50, 15, 0};
constexpr Rgb kYellow{45, 35, 0};
constexpr Rgb kCyan{0, 35, 45};
constexpr Rgb kDimWhite{12, 12, 12};
constexpr Rgb kWhite{40, 40, 40};
constexpr Rgb kOff{0, 0, 0};

constexpr uint32_t kFlashCount = static_cast<uint32_t>(LedFlash::kCount);

// Default length of each flash, indexed by LedFlash.
constexpr uint32_t kDefaultMs[kFlashCount] = {60, 120, 250, 300, 800, 800, 400};

std::atomic<uint8_t> s_mode{static_cast<uint8_t>(LedMode::kBooting)};
std::atomic<uint32_t> s_flash_until[kFlashCount];

Rgb scale(Rgb c, float k) {
  return Rgb{static_cast<uint8_t>(c.r * k), static_cast<uint8_t>(c.g * k), static_cast<uint8_t>(c.b * k)};
}

// 0.15..1.0 breathing curve.
float breathe(uint32_t now, uint32_t period_ms) {
  const float phase = static_cast<float>(now % period_ms) / period_ms;
  return 0.15f + 0.85f * (0.5f - 0.5f * std::cos(2.0f * 3.14159265f * phase));
}

bool flashActive(LedFlash f, uint32_t now) {
  const uint32_t until = s_flash_until[static_cast<uint32_t>(f)].load();
  return static_cast<int32_t>(until - now) > 0;
}

Rgb flashColor(LedFlash f, uint32_t now) {
  switch (f) {
    case LedFlash::kFiring: return kRed;
    case LedFlash::kWouldFire: return kMagenta;
    case LedFlash::kSuppressed: return kOrange;
    case LedFlash::kBark: return kYellow;
    case LedFlash::kClip: return kCyan;
    case LedFlash::kSound: return kDimWhite;
    case LedFlash::kButton: return ((now / 100) % 2 == 0) ? kWhite : kOff;
    default: return kOff;
  }
}

Rgb baseColor(LedMode mode, uint32_t now) {
  switch (mode) {
    case LedMode::kBooting: return scale(kBlue, breathe(now, 1000));
    case LedMode::kDryRun: return scale(kTeal, breathe(now, 3000));
    case LedMode::kArmed: return scale(kGreen, breathe(now, 3000));
    case LedMode::kCapture: return kPurple;
    case LedMode::kError: return ((now / 150) % 2 == 0) ? kRed : kOff;
  }
  return kOff;
}

void ledTask(void*) {
  // Highest priority first.
  constexpr LedFlash kPriority[] = {LedFlash::kButton, LedFlash::kFiring, LedFlash::kWouldFire,
                                    LedFlash::kSuppressed, LedFlash::kBark, LedFlash::kClip,
                                    LedFlash::kSound};
  Rgb last{1, 2, 3};
  for (;;) {
    const uint32_t now = millis();
    Rgb c = baseColor(static_cast<LedMode>(s_mode.load()), now);
    for (LedFlash f : kPriority) {
      if (flashActive(f, now)) {
        c = flashColor(f, now);
        break;
      }
    }
    if (c.r != last.r || c.g != last.g || c.b != last.b) {
      rgbLedWrite(kPinStatusLed, c.r, c.g, c.b);
      last = c;
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

}  // namespace

void ledBegin() {
  for (auto& f : s_flash_until) f.store(0);
  rgbLedWrite(kPinStatusLed, 0, 0, 0);
  xTaskCreatePinnedToCore(ledTask, "led", 3072, nullptr, 2, nullptr, 1);
}

void ledSetMode(LedMode mode) { s_mode.store(static_cast<uint8_t>(mode)); }

LedMode ledMode() { return static_cast<LedMode>(s_mode.load()); }

void ledFlash(LedFlash flash, uint32_t duration_ms) {
  const uint32_t i = static_cast<uint32_t>(flash);
  if (i >= kFlashCount) return;
  if (duration_ms == 0) duration_ms = kDefaultMs[i];
  s_flash_until[i].store(millis() + duration_ms);
}
