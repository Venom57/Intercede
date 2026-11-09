// Host-side unit tests for the board-independent logic in lib/bark_logic.
// Build and run with:  make -C test/host
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <vector>

#include "onset_detector.h"
#include "pcm.h"
#include "settings.h"
#include "sweep.h"
#include "trigger_policy.h"

using namespace bark;

static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond)                                                     \
  do {                                                                  \
    ++g_checks;                                                         \
    if (!(cond)) {                                                      \
      ++g_failures;                                                     \
      std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);     \
    }                                                                   \
  } while (0)

#define CHECK_EQ(a, b) CHECK((a) == (b))

// ---------------------------------------------------------------- audio synth

constexpr uint32_t kRate = 16000;

struct Lcg {
  uint32_t state = 12345;
  float next() {  // uniform in [-1, 1)
    state = state * 1664525u + 1013904223u;
    return static_cast<float>(state >> 8) / static_cast<float>(1u << 23) - 1.0f;
  }
};

// Background noise at roughly -60 dBFS.
void addNoise(std::vector<int16_t>& out, uint32_t ms, Lcg& rng, float amp = 30.0f) {
  const uint32_t n = kRate * ms / 1000;
  for (uint32_t i = 0; i < n; ++i) out.push_back(saturateToInt16(amp * rng.next()));
}

// A tone made of a fundamental plus harmonics, with short fades, over noise.
void addTone(std::vector<int16_t>& out, uint32_t ms, std::vector<float> freqs, float amp, Lcg& rng) {
  const uint32_t n = kRate * ms / 1000;
  const uint32_t fade = kRate / 200;  // 5 ms
  for (uint32_t i = 0; i < n; ++i) {
    float env = 1.0f;
    if (i < fade) env = static_cast<float>(i) / fade;
    if (n - i < fade) env = static_cast<float>(n - i) / fade;
    float v = 0.0f;
    for (size_t h = 0; h < freqs.size(); ++h) {
      v += std::sin(2.0f * kPi * freqs[h] * i / kRate) / (h + 1);
    }
    out.push_back(saturateToInt16(amp * env * v + 30.0f * rng.next()));
  }
}

std::vector<SoundEvent> runOnset(const std::vector<int16_t>& audio) {
  OnsetDetector det;
  std::vector<SoundEvent> events;
  // Feed in 10 ms chunks like the firmware does.
  for (size_t i = 0; i < audio.size(); i += 160) {
    const size_t n = std::min<size_t>(160, audio.size() - i);
    det.process(&audio[i], n);
    SoundEvent ev;
    while (det.popEvent(&ev)) events.push_back(ev);
  }
  return events;
}

// ---------------------------------------------------------------- tests

void testOnsetSilenceHasNoEvents() {
  std::puts("onset: steady background noise produces no events");
  Lcg rng;
  std::vector<int16_t> audio;
  addNoise(audio, 5000, rng);
  CHECK(runOnset(audio).empty());
}

void testOnsetBarkLikeBurst() {
  std::puts("onset: 200 ms harmonic burst is one bark_like event");
  Lcg rng;
  std::vector<int16_t> audio;
  addNoise(audio, 1500, rng);
  addTone(audio, 200, {600.0f, 1200.0f, 1800.0f}, 3000.0f, rng);
  addNoise(audio, 1000, rng);
  const auto events = runOnset(audio);
  CHECK_EQ(events.size(), 1u);
  if (!events.empty()) {
    CHECK(events[0].bark_like);
    CHECK(events[0].duration_ms >= 150 && events[0].duration_ms <= 260);
    CHECK(events[0].peak_over_floor_db > 30.0f);
    CHECK(events[0].band_ratio > 0.8f);
  }
}

void testOnsetTwoBarks() {
  std::puts("onset: bark, gap, bark gives two separate events");
  Lcg rng;
  std::vector<int16_t> audio;
  addNoise(audio, 1500, rng);
  addTone(audio, 180, {700.0f, 1400.0f}, 3000.0f, rng);
  addNoise(audio, 250, rng);
  addTone(audio, 220, {700.0f, 1400.0f}, 3000.0f, rng);
  addNoise(audio, 800, rng);
  const auto events = runOnset(audio);
  CHECK_EQ(events.size(), 2u);
  for (const auto& ev : events) CHECK(ev.bark_like);
}

void testOnsetLongSoundIsNotBarkLike() {
  std::puts("onset: 3 s continuous tone is not bark_like");
  Lcg rng;
  std::vector<int16_t> audio;
  addNoise(audio, 1500, rng);
  addTone(audio, 3000, {800.0f}, 3000.0f, rng);
  addNoise(audio, 3000, rng);
  const auto events = runOnset(audio);
  CHECK(!events.empty());
  for (const auto& ev : events) CHECK(!ev.bark_like);
}

void testOnsetLowRumbleIsNotBarkLike() {
  std::puts("onset: low-frequency thump (80 Hz) is not bark_like");
  Lcg rng;
  std::vector<int16_t> audio;
  addNoise(audio, 1500, rng);
  addTone(audio, 200, {80.0f}, 20000.0f, rng);
  addNoise(audio, 1000, rng);
  const auto events = runOnset(audio);
  CHECK_EQ(events.size(), 1u);  // it is loud enough to be an event...
  for (const auto& ev : events) CHECK(!ev.bark_like);  // ...but not in the bark band
}

void testOnsetHighPitchIsNotBarkLike() {
  std::puts("onset: 6 kHz squeak is not bark_like");
  Lcg rng;
  std::vector<int16_t> audio;
  addNoise(audio, 1500, rng);
  addTone(audio, 200, {6000.0f}, 8000.0f, rng);
  addNoise(audio, 1000, rng);
  const auto events = runOnset(audio);
  CHECK_EQ(events.size(), 1u);
  for (const auto& ev : events) CHECK(!ev.bark_like);
}

void testOnsetDigitalSilenceStart() {
  std::puts("onset: start-up zeros don't make background noise look like events");
  Lcg rng;
  std::vector<int16_t> audio(kRate / 2, 0);  // 500 ms of digital silence
  addNoise(audio, 5000, rng);
  const auto events = runOnset(audio);
  // The floor is clamped at -90 dBFS; noise at about -60 dBFS can open one
  // long event while the floor catches up, but it must not look like a bark.
  for (const auto& ev : events) CHECK(!ev.bark_like);
  CHECK(events.size() <= 1u);
}

void testPolicyKOfN() {
  std::puts("policy: needs k positives within n slices");
  TriggerPolicy p(PolicyConfig{0.8f, 2, 4, 5000, 12});
  uint64_t t = 1000;
  CHECK(p.update(t += 250, 0.9f) == Decision::kBark);
  CHECK(p.update(t += 250, 0.1f) == Decision::kIdle);
  CHECK(p.update(t += 250, 0.95f) == Decision::kTrigger);
  CHECK_EQ(p.triggersInLastHour(t), 1);
}

void testPolicyPositivesTooFarApart() {
  std::puts("policy: positives further apart than n slices never trigger");
  TriggerPolicy p(PolicyConfig{0.8f, 2, 3, 5000, 12});
  uint64_t t = 0;
  for (int round = 0; round < 5; ++round) {
    CHECK(p.update(t += 250, 0.9f) == Decision::kBark);
    CHECK(p.update(t += 250, 0.0f) == Decision::kIdle);
    CHECK(p.update(t += 250, 0.0f) == Decision::kIdle);
  }
  CHECK_EQ(p.triggersInLastHour(t), 0);
}

void testPolicyCooldown() {
  std::puts("policy: cooldown suppresses, then allows again");
  TriggerPolicy p(PolicyConfig{0.5f, 1, 1, 5000, 12});
  CHECK(p.update(10000, 1.0f) == Decision::kTrigger);
  CHECK(p.update(10250, 1.0f) == Decision::kSuppressedCooldown);
  CHECK(p.update(14999, 1.0f) == Decision::kSuppressedCooldown);
  CHECK(p.update(15000, 1.0f) == Decision::kTrigger);
}

void testPolicyHourlyCap() {
  std::puts("policy: hourly cap holds for a rolling hour");
  TriggerPolicy p(PolicyConfig{0.5f, 1, 1, 1000, 3});
  uint64_t t = 0;
  CHECK(p.update(t = 0, 1.0f) == Decision::kTrigger);
  CHECK(p.update(t = 2000, 1.0f) == Decision::kTrigger);
  CHECK(p.update(t = 4000, 1.0f) == Decision::kTrigger);
  CHECK(p.update(t = 6000, 1.0f) == Decision::kSuppressedHourlyCap);
  CHECK(p.update(t = TriggerPolicy::kHourMs - 1, 1.0f) == Decision::kSuppressedHourlyCap);
  // The first trigger (t=0) ages out at exactly one hour.
  CHECK(p.update(t = TriggerPolicy::kHourMs, 1.0f) == Decision::kTrigger);
  CHECK_EQ(p.triggersInLastHour(t), 3);
}

void testPolicyReconfigureKeepsHistory() {
  std::puts("policy: changing settings doesn't reset the hourly cap");
  TriggerPolicy p(PolicyConfig{0.5f, 1, 1, 1000, 1});
  CHECK(p.update(0, 1.0f) == Decision::kTrigger);
  p.configure(PolicyConfig{0.5f, 1, 1, 1000, 1});
  CHECK(p.update(5000, 1.0f) == Decision::kSuppressedHourlyCap);
}

void testPolicyWindowClearedAfterTrigger() {
  std::puts("policy: one bark episode triggers once");
  TriggerPolicy p(PolicyConfig{0.5f, 2, 4, 1000, 12});
  uint64_t t = 0;
  CHECK(p.update(t += 250, 1.0f) == Decision::kBark);
  CHECK(p.update(t += 250, 1.0f) == Decision::kTrigger);
  // Window was cleared, so the next positive starts a fresh count.
  CHECK(p.update(t += 250, 1.0f) == Decision::kBark);
}

void testSweep() {
  std::puts("sweep: triangle between lo and hi, clamped to safe range");
  CHECK_EQ(sweepFrequencyHz(0, 23000, 27000, 100), 23000u);
  CHECK_EQ(sweepFrequencyHz(50, 23000, 27000, 100), 27000u);
  CHECK_EQ(sweepFrequencyHz(25, 23000, 27000, 100), 25000u);
  CHECK_EQ(sweepFrequencyHz(75, 23000, 27000, 100), 25000u);
  CHECK_EQ(sweepFrequencyHz(100, 23000, 27000, 100), 23000u);
  uint32_t lo = 1u << 31, hi = 0;
  for (uint32_t t = 0; t < 1000; ++t) {
    const uint32_t f = sweepFrequencyHz(t, 23000, 27000, 120);
    lo = f < lo ? f : lo;
    hi = f > hi ? f : hi;
  }
  CHECK(lo >= 23000 && hi <= 27000);
  CHECK_EQ(clampUltrasonicHz(15000), kUltrasonicMinHz);
  CHECK_EQ(clampUltrasonicHz(90000), kUltrasonicMaxHz);
  CHECK_EQ(periodTicksForHz(10000000, 25000), 400u);
  CHECK_EQ(periodTicksForHz(10000000, 23000), 435u);
}

void testSettingsSanitize() {
  std::puts("settings: sanitize clamps to safe ranges");
  Settings s;
  s.threshold = 5.0f;
  s.n = 20;
  s.k = 15;
  s.max_per_hour = 500;
  s.burst_ms = 10000;
  s.cooldown_ms = 0;
  s.freq_lo_hz = 30000;
  s.freq_hi_hz = 10000;
  s = sanitize(s);
  CHECK(s.threshold <= limits::kThresholdMax);
  CHECK_EQ(s.n, TriggerPolicy::kMaxWindow);
  CHECK(s.k <= s.n);
  CHECK_EQ(s.max_per_hour, TriggerPolicy::kMaxPerHourLimit);
  CHECK_EQ(s.burst_ms, limits::kBurstMaxMs);
  CHECK(s.cooldown_ms >= s.burst_ms + limits::kCooldownGapMs);
  CHECK_EQ(s.freq_lo_hz, kUltrasonicMinHz);  // 10 kHz clamped up, then swapped
  CHECK_EQ(s.freq_hi_hz, 30000u);

  Settings d = sanitize(Settings());
  CHECK(!d.armed);  // ships in dry-run mode
  CHECK_EQ(d.cooldown_ms, Settings().cooldown_ms);
}

void testApplySetting() {
  std::puts("settings: console 'set' parsing");
  Settings s;
  CHECK(applySetting(s, "threshold", "0.65", nullptr) == SetResult::kOk);
  CHECK(std::fabs(s.threshold - 0.65f) < 1e-6f);
  CHECK(applySetting(s, "threshold", "abc", nullptr) == SetResult::kBadValue);
  CHECK(applySetting(s, "threshold", "nan", nullptr) == SetResult::kBadValue);
  CHECK(applySetting(s, "freq", "24000", "26000") == SetResult::kOk);
  CHECK_EQ(s.freq_lo_hz, 24000u);
  CHECK_EQ(s.freq_hi_hz, 26000u);
  CHECK(applySetting(s, "freq", "24000", nullptr) == SetResult::kBadValue);
  CHECK(applySetting(s, "freq", "1000", "2000") == SetResult::kOk);
  CHECK_EQ(s.freq_lo_hz, kUltrasonicMinHz);  // never audible
  CHECK(applySetting(s, "k", "3", nullptr) == SetResult::kOk);
  CHECK(applySetting(s, "n", "2", nullptr) == SetResult::kOk);
  CHECK(s.k <= s.n);
  CHECK(applySetting(s, "volume", "11", nullptr) == SetResult::kUnknownKey);
  CHECK(applySetting(s, "burst", "1500", nullptr) == SetResult::kOk);
  CHECK(applySetting(s, "cooldown", "1000", nullptr) == SetResult::kOk);
  CHECK_EQ(s.cooldown_ms, 2500u);
}

void testPcm() {
  std::puts("pcm: I2S word conversion, saturation, DC removal");
  CHECK_EQ(saturateToInt16(40000.0f), 32767);
  CHECK_EQ(saturateToInt16(-40000.0f), -32768);
  CHECK_EQ(i2sWordToFloat(0x7FFFFF00, 16), 32767.0f);
  CHECK_EQ(i2sWordToFloat(static_cast<int32_t>(0x80000000), 16), -32768.0f);
  DcBlocker dc;
  float y = 0.0f;
  for (int i = 0; i < 16000; ++i) y = dc.process(1000.0f);
  CHECK(std::fabs(y) < 1.0f);
}

int main() {
  testOnsetSilenceHasNoEvents();
  testOnsetBarkLikeBurst();
  testOnsetTwoBarks();
  testOnsetLongSoundIsNotBarkLike();
  testOnsetLowRumbleIsNotBarkLike();
  testOnsetHighPitchIsNotBarkLike();
  testOnsetDigitalSilenceStart();
  testPolicyKOfN();
  testPolicyPositivesTooFarApart();
  testPolicyCooldown();
  testPolicyHourlyCap();
  testPolicyReconfigureKeepsHistory();
  testPolicyWindowClearedAfterTrigger();
  testSweep();
  testSettingsSanitize();
  testApplySetting();
  testPcm();
  std::printf("\n%d checks, %d failures\n", g_checks, g_failures);
  return g_failures == 0 ? 0 : 1;
}
