// Decides when a run of bark scores should turn into an ultrasonic burst.
//
// The detector produces one score (0..1) per audio slice (~250 ms). This class
// applies the safety rules on top of it:
//   * k-of-n smoothing: fire only if k of the last n slices were positive,
//     so one noisy slice can't set it off.
//   * cooldown: minimum time between bursts.
//   * hourly cap: at most N bursts in any rolling 60 minutes.
//
// Pure C++ with caller-supplied timestamps so it can be unit tested on a PC.
#pragma once

#include <cstdint>

namespace bark {

enum class Decision : uint8_t {
  kIdle,                 // slice was not a bark
  kBark,                 // bark-positive slice, not enough of them yet to fire
  kTrigger,              // fire now (or log "would fire" in dry-run mode)
  kSuppressedCooldown,   // would fire, but still inside the cooldown
  kSuppressedHourlyCap,  // would fire, but the hourly cap is used up
};

const char* decisionName(Decision d);

struct PolicyConfig {
  float threshold = 0.80f;      // score >= threshold counts as a positive slice
  uint8_t k = 2;                // positives needed...
  uint8_t n = 4;                // ...within the last n slices
  uint32_t cooldown_ms = 5000;  // minimum gap between triggers
  uint16_t max_per_hour = 12;   // triggers allowed in any rolling hour
};

class TriggerPolicy {
 public:
  static constexpr uint8_t kMaxWindow = 8;
  static constexpr uint16_t kMaxPerHourLimit = 60;
  static constexpr uint64_t kHourMs = 60ull * 60ull * 1000ull;

  TriggerPolicy() { configure(PolicyConfig()); }
  explicit TriggerPolicy(const PolicyConfig& cfg) { configure(cfg); }

  // Applies new settings. Clears the k-of-n window but keeps the trigger
  // history, so changing settings can't be used to dodge the hourly cap.
  void configure(const PolicyConfig& cfg);

  // Feed one slice score. now_ms must not go backwards.
  Decision update(uint64_t now_ms, float score);

  uint16_t triggersInLastHour(uint64_t now_ms) const;
  const PolicyConfig& config() const { return cfg_; }

 private:
  void prune(uint64_t now_ms);
  uint8_t positivesInWindow() const;
  void clearWindow();

  PolicyConfig cfg_;
  bool window_[kMaxWindow] = {};
  uint8_t win_pos_ = 0;
  bool has_last_trigger_ = false;
  uint64_t last_trigger_ms_ = 0;
  uint64_t history_[kMaxPerHourLimit] = {};  // trigger times, oldest first
  uint16_t history_count_ = 0;
};

}  // namespace bark
