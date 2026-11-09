// Finds short, loud sounds in the bark frequency band.
//
// Works on 10 ms frames: it band-passes the audio (default 400 Hz - 4 kHz,
// where most bark energy is), tracks the background noise floor, and reports
// an "event" whenever the band level jumps well above that floor and then
// falls back. Each finished event is labelled bark_like when its duration
// and in-band energy share look like a bark.
//
// This is NOT a bark classifier. It can't tell a bark from a clap or a shout.
// The firmware uses it for two things:
//   1. picking which sounds to save as training clips (capture mode), and
//   2. a placeholder detector so the whole device can be tested before you
//      have trained a model.
#pragma once

#include <cstddef>
#include <cstdint>

#include "biquad.h"

namespace bark {

struct OnsetConfig {
  uint32_t sample_rate_hz = 16000;
  uint16_t frame_ms = 10;
  float band_lo_hz = 400.0f;
  float band_hi_hz = 4000.0f;
  float onset_db = 15.0f;          // event starts this far above the noise floor
  float release_db = 8.0f;         // event ends when the level drops below floor + this
  uint16_t release_frames = 3;     // ...for this many frames in a row
  uint16_t min_event_ms = 60;      // bark_like duration window
  uint16_t max_event_ms = 800;
  float min_band_ratio = 0.5f;     // share of event energy that must be in the band
  float floor_rise_tc_s = 10.0f;   // how slowly the floor follows a rising background
  uint16_t warmup_frames = 50;     // frames used to learn the initial floor (no events)
};

struct SoundEvent {
  uint32_t duration_ms = 0;
  float peak_over_floor_db = 0.0f;
  float band_ratio = 0.0f;
  bool bark_like = false;
};

class OnsetDetector {
 public:
  explicit OnsetDetector(const OnsetConfig& cfg = OnsetConfig());

  // Feeds samples. Returns true if an event started during this call.
  bool process(const int16_t* samples, size_t count);

  // Pops the oldest finished event. Returns false when none are queued.
  bool popEvent(SoundEvent* out);

  bool inEvent() const { return in_event_; }
  float floorDb() const { return floor_db_; }  // background level, dBFS
  float levelDb() const { return level_db_; }  // band level of the last frame, dBFS
  void reset();

 private:
  bool endFrame();
  void finishEvent();
  void pushEvent(const SoundEvent& ev);

  static constexpr uint8_t kQueueSize = 4;

  OnsetConfig cfg_;
  Biquad hp_;
  Biquad lp_;
  uint32_t frame_len_;
  uint32_t frame_fill_ = 0;
  double band_acc_ = 0.0;
  double full_acc_ = 0.0;
  uint32_t frames_seen_ = 0;

  float floor_db_ = -90.0f;
  float level_db_ = -90.0f;

  bool in_event_ = false;
  uint32_t event_frames_ = 0;
  uint16_t below_count_ = 0;
  float event_floor_db_ = 0.0f;
  float peak_db_ = -120.0f;
  double ev_band_ = 0.0;
  double ev_full_ = 0.0;

  SoundEvent queue_[kQueueSize];
  uint8_t q_head_ = 0;
  uint8_t q_count_ = 0;
};

}  // namespace bark
