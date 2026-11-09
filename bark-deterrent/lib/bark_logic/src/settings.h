// Runtime settings (changed over the serial console, saved to flash) and the
// limits that keep them in a safe range.
#pragma once

#include <cstdint>

#include "trigger_policy.h"

namespace bark {

struct Settings {
  bool armed = false;               // false = dry run: log "would fire", never emit
  float threshold = 0.80f;          // bark score needed for a positive slice
  uint8_t k = 2;                    // positives needed...
  uint8_t n = 4;                    // ...within the last n slices (250 ms each)
  uint32_t cooldown_ms = 5000;      // min time between bursts
  uint16_t max_per_hour = 12;       // bursts allowed in any rolling hour
  uint16_t burst_ms = 800;          // length of one ultrasonic burst
  uint32_t freq_lo_hz = 23000;      // sweep range
  uint32_t freq_hi_hz = 27000;
  uint16_t sweep_period_ms = 120;   // one lo->hi->lo cycle
};

namespace limits {
constexpr float kThresholdMin = 0.05f, kThresholdMax = 0.99f;
constexpr uint32_t kCooldownMinMs = 1000, kCooldownMaxMs = 600000;
constexpr uint16_t kBurstMinMs = 100, kBurstMaxMs = 2000;
constexpr uint16_t kSweepMinMs = 20, kSweepMaxMs = 2000;
// Cooldown always covers the burst plus this gap, so bursts can't run back to back.
constexpr uint32_t kCooldownGapMs = 1000;
}  // namespace limits

// Clamps every field into its safe range and fixes inconsistent combinations.
Settings sanitize(Settings s);

PolicyConfig toPolicyConfig(const Settings& s);

enum class SetResult : uint8_t { kOk, kUnknownKey, kBadValue };

// Applies a console "set <key> <value> [value2]" command, then sanitizes.
// Keys: threshold, k, n, cooldown, maxhour, burst, freq (lo hi), sweep.
SetResult applySetting(Settings& s, const char* key, const char* value, const char* value2);

}  // namespace bark
