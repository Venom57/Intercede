#include "settings.h"

#include <cmath>
#include <cstdlib>
#include <cstring>

#include "sweep.h"

namespace bark {

namespace {

template <typename T>
T clampValue(T v, T lo, T hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

bool parseLong(const char* text, long* out) {
  if (text == nullptr || *text == '\0') return false;
  char* end = nullptr;
  const long v = std::strtol(text, &end, 10);
  if (*end != '\0') return false;
  *out = v;
  return true;
}

bool parseFloat(const char* text, float* out) {
  if (text == nullptr || *text == '\0') return false;
  char* end = nullptr;
  const float v = std::strtof(text, &end);
  if (*end != '\0' || !std::isfinite(v)) return false;
  *out = v;
  return true;
}

}  // namespace

Settings sanitize(Settings s) {
  if (!std::isfinite(s.threshold)) s.threshold = Settings().threshold;
  s.threshold = clampValue(s.threshold, limits::kThresholdMin, limits::kThresholdMax);
  s.n = clampValue<uint8_t>(s.n, 1, TriggerPolicy::kMaxWindow);
  s.k = clampValue<uint8_t>(s.k, 1, s.n);
  s.max_per_hour = clampValue<uint16_t>(s.max_per_hour, 1, TriggerPolicy::kMaxPerHourLimit);
  s.burst_ms = clampValue(s.burst_ms, limits::kBurstMinMs, limits::kBurstMaxMs);
  s.cooldown_ms = clampValue(s.cooldown_ms, limits::kCooldownMinMs, limits::kCooldownMaxMs);
  if (s.cooldown_ms < s.burst_ms + limits::kCooldownGapMs) {
    s.cooldown_ms = s.burst_ms + limits::kCooldownGapMs;
  }
  s.freq_lo_hz = clampUltrasonicHz(s.freq_lo_hz);
  s.freq_hi_hz = clampUltrasonicHz(s.freq_hi_hz);
  if (s.freq_lo_hz > s.freq_hi_hz) {
    const uint32_t t = s.freq_lo_hz;
    s.freq_lo_hz = s.freq_hi_hz;
    s.freq_hi_hz = t;
  }
  s.sweep_period_ms = clampValue(s.sweep_period_ms, limits::kSweepMinMs, limits::kSweepMaxMs);
  return s;
}

PolicyConfig toPolicyConfig(const Settings& s) {
  PolicyConfig c;
  c.threshold = s.threshold;
  c.k = s.k;
  c.n = s.n;
  c.cooldown_ms = s.cooldown_ms;
  c.max_per_hour = s.max_per_hour;
  return c;
}

SetResult applySetting(Settings& s, const char* key, const char* value, const char* value2) {
  if (key == nullptr) return SetResult::kUnknownKey;
  long iv = 0;
  float fv = 0.0f;

  if (std::strcmp(key, "threshold") == 0) {
    if (!parseFloat(value, &fv)) return SetResult::kBadValue;
    s.threshold = fv;
  } else if (std::strcmp(key, "k") == 0) {
    if (!parseLong(value, &iv) || iv < 1 || iv > 255) return SetResult::kBadValue;
    s.k = static_cast<uint8_t>(iv);
  } else if (std::strcmp(key, "n") == 0) {
    if (!parseLong(value, &iv) || iv < 1 || iv > 255) return SetResult::kBadValue;
    s.n = static_cast<uint8_t>(iv);
  } else if (std::strcmp(key, "cooldown") == 0) {
    if (!parseLong(value, &iv) || iv < 0) return SetResult::kBadValue;
    s.cooldown_ms = static_cast<uint32_t>(iv);
  } else if (std::strcmp(key, "maxhour") == 0) {
    if (!parseLong(value, &iv) || iv < 0 || iv > 65535) return SetResult::kBadValue;
    s.max_per_hour = static_cast<uint16_t>(iv);
  } else if (std::strcmp(key, "burst") == 0) {
    if (!parseLong(value, &iv) || iv < 0 || iv > 65535) return SetResult::kBadValue;
    s.burst_ms = static_cast<uint16_t>(iv);
  } else if (std::strcmp(key, "freq") == 0) {
    long hi = 0;
    if (!parseLong(value, &iv) || !parseLong(value2, &hi) || iv <= 0 || hi <= 0) {
      return SetResult::kBadValue;
    }
    s.freq_lo_hz = static_cast<uint32_t>(iv);
    s.freq_hi_hz = static_cast<uint32_t>(hi);
  } else if (std::strcmp(key, "sweep") == 0) {
    if (!parseLong(value, &iv) || iv < 0 || iv > 65535) return SetResult::kBadValue;
    s.sweep_period_ms = static_cast<uint16_t>(iv);
  } else {
    return SetResult::kUnknownKey;
  }
  s = sanitize(s);
  return SetResult::kOk;
}

}  // namespace bark
