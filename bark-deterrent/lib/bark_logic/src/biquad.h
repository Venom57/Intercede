// Second-order IIR filter (biquad) with RBJ "Audio EQ Cookbook" coefficients.
// Pure C++ so it can be unit tested on a PC.
#pragma once

#include <cmath>

namespace bark {

constexpr float kPi = 3.14159265358979f;

class Biquad {
 public:
  Biquad() = default;

  static Biquad lowPass(float sample_rate_hz, float cutoff_hz, float q = 0.70710678f) {
    const float w0 = 2.0f * kPi * cutoff_hz / sample_rate_hz;
    const float c = std::cos(w0);
    const float alpha = std::sin(w0) / (2.0f * q);
    return Biquad((1.0f - c) / 2.0f, 1.0f - c, (1.0f - c) / 2.0f, 1.0f + alpha, -2.0f * c, 1.0f - alpha);
  }

  static Biquad highPass(float sample_rate_hz, float cutoff_hz, float q = 0.70710678f) {
    const float w0 = 2.0f * kPi * cutoff_hz / sample_rate_hz;
    const float c = std::cos(w0);
    const float alpha = std::sin(w0) / (2.0f * q);
    return Biquad((1.0f + c) / 2.0f, -(1.0f + c), (1.0f + c) / 2.0f, 1.0f + alpha, -2.0f * c, 1.0f - alpha);
  }

  float process(float x) {
    const float y = b0_ * x + b1_ * x1_ + b2_ * x2_ - a1_ * y1_ - a2_ * y2_;
    x2_ = x1_;
    x1_ = x;
    y2_ = y1_;
    y1_ = y;
    return y;
  }

  void reset() { x1_ = x2_ = y1_ = y2_ = 0.0f; }

 private:
  Biquad(float b0, float b1, float b2, float a0, float a1, float a2)
      : b0_(b0 / a0), b1_(b1 / a0), b2_(b2 / a0), a1_(a1 / a0), a2_(a2 / a0) {}

  float b0_ = 1.0f, b1_ = 0.0f, b2_ = 0.0f, a1_ = 0.0f, a2_ = 0.0f;
  float x1_ = 0.0f, x2_ = 0.0f, y1_ = 0.0f, y2_ = 0.0f;
};

}  // namespace bark
