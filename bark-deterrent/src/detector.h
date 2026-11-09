// Bark detector front end.
//
// If an Edge Impulse Arduino library named `bark_detector_inferencing` is in
// lib/, it's used automatically. Otherwise the firmware falls back to the
// placeholder detector (OnsetDetector), which fires on any short, loud,
// bark-band sound. It is good enough to test the hardware, not to train a dog.
#pragma once

#include <cstddef>
#include <cstdint>

struct DetectorInfo {
  const char* name;
  bool is_ml;             // true = trained model, false = placeholder
  size_t slice_samples;   // samples per detectorRunSlice() call
};

bool detectorBegin();
const DetectorInfo& detectorInfo();

// Scores one slice of audio. Returns the bark score in [0, 1], or:
//   -1  inference error
//   -2  the model has no class named BARK_LABEL (see config.h)
// Slices must be consecutive audio.
float detectorRunSlice(const int16_t* slice);
