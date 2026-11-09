#include "onset_detector.h"

#include <cmath>

namespace bark {

namespace {

constexpr double kFullScaleSq = 32768.0 * 32768.0;
// Digital silence (e.g. the first DMA buffers after start-up) must not pull the
// floor so low that every sound afterwards looks like an event.
constexpr float kMinFloorDb = -90.0f;

float toDbfs(double mean_square) {
  return static_cast<float>(10.0 * std::log10(mean_square / kFullScaleSq + 1e-12));
}

}  // namespace

OnsetDetector::OnsetDetector(const OnsetConfig& cfg)
    : cfg_(cfg),
      hp_(Biquad::highPass(static_cast<float>(cfg.sample_rate_hz), cfg.band_lo_hz)),
      lp_(Biquad::lowPass(static_cast<float>(cfg.sample_rate_hz), cfg.band_hi_hz)),
      frame_len_(cfg.sample_rate_hz * cfg.frame_ms / 1000) {
  if (frame_len_ == 0) frame_len_ = 1;
}

void OnsetDetector::reset() {
  hp_.reset();
  lp_.reset();
  frame_fill_ = 0;
  band_acc_ = full_acc_ = 0.0;
  frames_seen_ = 0;
  floor_db_ = level_db_ = -90.0f;
  in_event_ = false;
  q_head_ = q_count_ = 0;
}

bool OnsetDetector::process(const int16_t* samples, size_t count) {
  bool started = false;
  for (size_t i = 0; i < count; ++i) {
    const float x = static_cast<float>(samples[i]);
    const float band = lp_.process(hp_.process(x));
    band_acc_ += static_cast<double>(band) * band;
    full_acc_ += static_cast<double>(x) * x;
    if (++frame_fill_ >= frame_len_) {
      started |= endFrame();
      frame_fill_ = 0;
      band_acc_ = full_acc_ = 0.0;
    }
  }
  return started;
}

bool OnsetDetector::endFrame() {
  level_db_ = toDbfs(band_acc_ / frame_len_);
  const float frame_s = cfg_.frame_ms / 1000.0f;
  const float rise_alpha = frame_s / cfg_.floor_rise_tc_s;

  // Learn the starting floor from the quietest frames, without raising events.
  if (frames_seen_ < cfg_.warmup_frames) {
    floor_db_ = (frames_seen_ == 0 || level_db_ < floor_db_) ? level_db_ : floor_db_;
    if (floor_db_ < kMinFloorDb) floor_db_ = kMinFloorDb;
    ++frames_seen_;
    return false;
  }

  if (!in_event_) {
    if (level_db_ > floor_db_ + cfg_.onset_db) {
      in_event_ = true;
      event_frames_ = 1;
      below_count_ = 0;
      event_floor_db_ = floor_db_;
      peak_db_ = level_db_;
      ev_band_ = band_acc_;
      ev_full_ = full_acc_;
      return true;
    }
    // Falls quickly (a quiet moment is real), rises slowly (so barks don't
    // drag the floor up with them).
    if (level_db_ < floor_db_) {
      floor_db_ += 0.3f * (level_db_ - floor_db_);
    } else {
      floor_db_ += rise_alpha * (level_db_ - floor_db_);
    }
    if (floor_db_ < kMinFloorDb) floor_db_ = kMinFloorDb;
    return false;
  }

  // Inside an event.
  ++event_frames_;
  ev_band_ += band_acc_;
  ev_full_ += full_acc_;
  if (level_db_ > peak_db_) peak_db_ = level_db_;

  // A sound that goes on far longer than a bark (mower, music) is treated as
  // the new background so the event can end.
  if (event_frames_ * cfg_.frame_ms > cfg_.max_event_ms) {
    floor_db_ += 10.0f * rise_alpha * (level_db_ - floor_db_);
  }

  if (level_db_ < floor_db_ + cfg_.release_db) {
    if (++below_count_ >= cfg_.release_frames) finishEvent();
  } else {
    below_count_ = 0;
  }
  return false;
}

void OnsetDetector::finishEvent() {
  SoundEvent ev;
  const uint32_t active_frames = event_frames_ > below_count_ ? event_frames_ - below_count_ : 0;
  ev.duration_ms = active_frames * cfg_.frame_ms;
  ev.peak_over_floor_db = peak_db_ - event_floor_db_;
  ev.band_ratio = ev_full_ > 0.0 ? static_cast<float>(ev_band_ / ev_full_) : 0.0f;
  ev.bark_like = ev.duration_ms >= cfg_.min_event_ms && ev.duration_ms <= cfg_.max_event_ms &&
                 ev.band_ratio >= cfg_.min_band_ratio;
  pushEvent(ev);
  in_event_ = false;
}

void OnsetDetector::pushEvent(const SoundEvent& ev) {
  if (q_count_ == kQueueSize) {  // drop the oldest
    q_head_ = static_cast<uint8_t>((q_head_ + 1) % kQueueSize);
    --q_count_;
  }
  queue_[(q_head_ + q_count_) % kQueueSize] = ev;
  ++q_count_;
}

bool OnsetDetector::popEvent(SoundEvent* out) {
  if (q_count_ == 0) return false;
  *out = queue_[q_head_];
  q_head_ = static_cast<uint8_t>((q_head_ + 1) % kQueueSize);
  --q_count_;
  return true;
}

}  // namespace bark
