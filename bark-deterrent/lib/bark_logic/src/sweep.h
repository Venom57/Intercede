// Frequency sweep math for the ultrasonic output.
#pragma once

#include <cstdint>

namespace bark {

// Hard limits for the ultrasonic output. The firmware never generates anything
// outside this range, whatever the settings say. 22 kHz keeps the output above
// what most people can hear; piezo tweeters lose output well before 40 kHz.
constexpr uint32_t kUltrasonicMinHz = 22000;
constexpr uint32_t kUltrasonicMaxHz = 40000;

inline uint32_t clampUltrasonicHz(uint32_t hz) {
  if (hz < kUltrasonicMinHz) return kUltrasonicMinHz;
  if (hz > kUltrasonicMaxHz) return kUltrasonicMaxHz;
  return hz;
}

// Triangle sweep: lo -> hi during the first half of the period, hi -> lo during
// the second half. Sweeping hits the transducer's resonance and makes the tone
// harder to tune out than a fixed frequency.
inline uint32_t sweepFrequencyHz(uint32_t t_ms, uint32_t lo_hz, uint32_t hi_hz, uint32_t period_ms) {
  if (hi_hz <= lo_hz || period_ms < 2) return lo_hz;
  const uint32_t phase = t_ms % period_ms;
  const uint32_t half = period_ms / 2;
  const uint64_t span = hi_hz - lo_hz;
  if (phase < half) return lo_hz + static_cast<uint32_t>(span * phase / half);
  return hi_hz - static_cast<uint32_t>(span * (phase - half) / (period_ms - half));
}

// Timer ticks per PWM period for a given output frequency (rounded to nearest).
inline uint32_t periodTicksForHz(uint32_t timer_resolution_hz, uint32_t hz) {
  return (timer_resolution_hz + hz / 2) / hz;
}

}  // namespace bark
