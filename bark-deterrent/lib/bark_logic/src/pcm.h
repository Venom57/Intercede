// Helpers for turning raw I2S microphone words into clean 16-bit PCM.
#pragma once

#include <cstdint>

namespace bark {

// Removes the DC offset MEMS microphones add (one-pole high-pass, ~13 Hz at 16 kHz).
class DcBlocker {
 public:
  explicit DcBlocker(float r = 0.995f) : r_(r) {}
  float process(float x) {
    const float y = x - x1_ + r_ * y1_;
    x1_ = x;
    y1_ = y;
    return y;
  }
  void reset() { x1_ = y1_ = 0.0f; }

 private:
  float r_;
  float x1_ = 0.0f;
  float y1_ = 0.0f;
};

inline int16_t saturateToInt16(float v) {
  if (v > 32767.0f) return 32767;
  if (v < -32768.0f) return -32768;
  return static_cast<int16_t>(v);
}

// The INMP441 sends 24-bit samples left-aligned in a 32-bit I2S slot.
// Shifting right by 16 gives exact 16-bit PCM; each step less adds 6 dB of gain.
inline float i2sWordToFloat(int32_t word, int shift) {
  return static_cast<float>(word >> shift);
}

}  // namespace bark
