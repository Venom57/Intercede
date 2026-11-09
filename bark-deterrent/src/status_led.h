// Onboard WS2812 status feedback.
//
// Background (what mode the device is in):
//   blue, slow pulse ........ booting / learning the noise floor
//   teal, slow breathe ...... listening, DRY RUN (never emits ultrasound)
//   green, slow breathe ..... listening, ARMED
//   purple, steady .......... capture/record mode (streaming clips to the PC)
//   red, fast blink ......... hardware error (see serial log)
//
// Flashes on top (highest priority wins):
//   red, solid .............. ultrasonic burst in progress
//   magenta ................. dry run: it WOULD have fired
//   orange .................. bark episode, but blocked by cooldown / hourly cap
//   yellow .................. bark-positive slice
//   cyan blip ............... training clip saved
//   dim white blip .......... loud sound (not necessarily a bark)
//   white double blink ...... button press acknowledged
#pragma once

#include <cstdint>

enum class LedMode : uint8_t { kBooting, kDryRun, kArmed, kCapture, kError };

enum class LedFlash : uint8_t {
  kSound,
  kClip,
  kBark,
  kSuppressed,
  kWouldFire,
  kFiring,
  kButton,
  kCount,
};

void ledBegin();
void ledSetMode(LedMode mode);
LedMode ledMode();
// Shows `flash` for `duration_ms` (0 = the flash's default length).
void ledFlash(LedFlash flash, uint32_t duration_ms = 0);
