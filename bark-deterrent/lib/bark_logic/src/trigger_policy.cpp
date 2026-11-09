#include "trigger_policy.h"

namespace bark {

const char* decisionName(Decision d) {
  switch (d) {
    case Decision::kIdle: return "idle";
    case Decision::kBark: return "bark";
    case Decision::kTrigger: return "trigger";
    case Decision::kSuppressedCooldown: return "suppressed_cooldown";
    case Decision::kSuppressedHourlyCap: return "suppressed_hourly_cap";
  }
  return "?";
}

void TriggerPolicy::configure(const PolicyConfig& cfg) {
  cfg_ = cfg;
  if (cfg_.n < 1) cfg_.n = 1;
  if (cfg_.n > kMaxWindow) cfg_.n = kMaxWindow;
  if (cfg_.k < 1) cfg_.k = 1;
  if (cfg_.k > cfg_.n) cfg_.k = cfg_.n;
  if (cfg_.max_per_hour > kMaxPerHourLimit) cfg_.max_per_hour = kMaxPerHourLimit;
  clearWindow();
}

void TriggerPolicy::clearWindow() {
  for (bool& w : window_) w = false;
  win_pos_ = 0;
}

uint8_t TriggerPolicy::positivesInWindow() const {
  uint8_t count = 0;
  for (uint8_t i = 0; i < cfg_.n; ++i) count += window_[i] ? 1 : 0;
  return count;
}

void TriggerPolicy::prune(uint64_t now_ms) {
  uint16_t drop = 0;
  while (drop < history_count_ && now_ms - history_[drop] >= kHourMs) ++drop;
  if (drop == 0) return;
  for (uint16_t i = drop; i < history_count_; ++i) history_[i - drop] = history_[i];
  history_count_ -= drop;
}

uint16_t TriggerPolicy::triggersInLastHour(uint64_t now_ms) const {
  uint16_t count = 0;
  for (uint16_t i = 0; i < history_count_; ++i) {
    if (now_ms - history_[i] < kHourMs) ++count;
  }
  return count;
}

Decision TriggerPolicy::update(uint64_t now_ms, float score) {
  prune(now_ms);

  const bool positive = score >= cfg_.threshold;
  window_[win_pos_] = positive;
  win_pos_ = static_cast<uint8_t>((win_pos_ + 1) % cfg_.n);

  if (!positive) return Decision::kIdle;
  if (positivesInWindow() < cfg_.k) return Decision::kBark;

  if (has_last_trigger_ && now_ms - last_trigger_ms_ < cfg_.cooldown_ms) {
    return Decision::kSuppressedCooldown;
  }
  if (history_count_ >= cfg_.max_per_hour) return Decision::kSuppressedHourlyCap;

  has_last_trigger_ = true;
  last_trigger_ms_ = now_ms;
  history_[history_count_++] = now_ms;
  clearWindow();  // the same bark must not trigger twice
  return Decision::kTrigger;
}

}  // namespace bark
