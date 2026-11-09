#include "detector.h"

#include "config.h"

// ---------------------------------------------------------------------------
// Edge Impulse model (used when the exported library is in lib/)
//
// Name your Edge Impulse project "bark_detector" and the exported Arduino
// library is called bark_detector_inferencing. If yours has a different
// name, change it in BOTH lines below.
// ---------------------------------------------------------------------------
#if __has_include(<bark_detector_inferencing.h>) && !defined(BARK_FORCE_PLACEHOLDER)
#define EIDSP_QUANTIZE_FILTERBANK 0
#define EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW 4
#include <bark_detector_inferencing.h>
#define BARK_HAVE_EI 1
#else
#define BARK_HAVE_EI 0
#endif

#if BARK_HAVE_EI

#include <cstring>

static_assert(EI_CLASSIFIER_FREQUENCY == kSampleRateHz,
              "Edge Impulse model sample rate must match kSampleRateHz in config.h");

namespace {

const DetectorInfo kInfo{"edge-impulse", true, EI_CLASSIFIER_SLICE_SIZE};
const int16_t* s_slice = nullptr;
int s_bark_index = -1;  // resolved from the first result's labels

int getSliceData(size_t offset, size_t length, float* out) {
  numpy::int16_to_float(s_slice + offset, out, length);
  return 0;
}

}  // namespace

bool detectorBegin() {
  run_classifier_init();
  return true;
}

const DetectorInfo& detectorInfo() { return kInfo; }

float detectorRunSlice(const int16_t* slice) {
  s_slice = slice;
  signal_t signal;
  signal.total_length = EI_CLASSIFIER_SLICE_SIZE;
  signal.get_data = &getSliceData;

  ei_impulse_result_t result = {};
  if (run_classifier_continuous(&signal, &result, false) != EI_IMPULSE_OK) return -1.0f;

  if (s_bark_index < 0) {
    for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; ++i) {
      if (std::strcmp(result.classification[i].label, BARK_LABEL) == 0) s_bark_index = static_cast<int>(i);
    }
    if (s_bark_index < 0) return -2.0f;  // model has no BARK_LABEL class
  }
  return result.classification[s_bark_index].value;
}

#else  // ------------------------------------------------- placeholder detector

#include "onset_detector.h"

namespace {

const DetectorInfo kInfo{"placeholder (loud bark-band sounds, not ML)", false, kDefaultSliceSamples};
bark::OnsetDetector s_onset;

}  // namespace

bool detectorBegin() { return true; }

const DetectorInfo& detectorInfo() { return kInfo; }

float detectorRunSlice(const int16_t* slice) {
  s_onset.process(slice, kInfo.slice_samples);
  float score = 0.0f;
  bark::SoundEvent ev;
  while (s_onset.popEvent(&ev)) {
    if (ev.bark_like) score = 1.0f;
  }
  return score;
}

#endif
