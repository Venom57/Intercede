// Ultrasonic burst output: MCPWM drives an H-bridge with two complementary
// square waves, sweeping between two frequencies.
#pragma once

#include <cstdint>

struct BurstParams {
  uint16_t duration_ms;
  uint32_t freq_lo_hz;
  uint32_t freq_hi_hz;
  uint16_t sweep_period_ms;
};

// Sets up MCPWM with both bridge inputs forced low. Returns false on error.
bool ultrasonicBegin();

// Starts a burst in the background. Duration and frequencies are clamped to
// the safe limits. Returns false if a burst is already running or the driver
// isn't ready.
bool ultrasonicBurst(const BurstParams& params);

bool ultrasonicActive();

// Uptime (ms, low 32 bits) when the last burst ended, 0 if none yet.
// Compare with unsigned subtraction: (now32 - ultrasonicLastEndMs()) < guard.
uint32_t ultrasonicLastEndMs();
