// Board pins and compile-time constants.
// Runtime-tunable settings (threshold, cooldown, frequencies...) live in
// lib/bark_logic/src/settings.h and are changed from the serial console.
#pragma once

#include <cstdint>

// ---------------------------------------------------------------------------
// Board: Lonely Binary ESP32-S3 (N16R8: 16 MB flash, 8 MB octal PSRAM)
//
// Avoid: GPIO 19/20 (USB), 43/44 (USB-UART bridge), 26-37 (flash + octal
// PSRAM), 0/3/45/46 (strapping; 0 is the BOOT button, used here as an input).
// ---------------------------------------------------------------------------
constexpr int kPinStatusLed = 48;  // onboard WS2812 "NeoPixel"
constexpr int kPinButton = 0;      // BOOT button, active low

// INMP441 I2S MEMS microphone (L/R pin tied to GND = left channel).
constexpr int kPinMicBclk = 4;  // INMP441 SCK
constexpr int kPinMicWs = 5;    // INMP441 WS
constexpr int kPinMicData = 6;  // INMP441 SD

// H-bridge inputs driving the piezo (e.g. DRV8871 IN1/IN2).
// Both low = bridge off. The firmware forces both low whenever it isn't
// bursting, including at boot.
constexpr int kPinBridgeIn1 = 12;
constexpr int kPinBridgeIn2 = 13;

// ---------------------------------------------------------------------------
// Audio
// ---------------------------------------------------------------------------
constexpr uint32_t kSampleRateHz = 16000;  // must match the Edge Impulse model
constexpr uint32_t kChunkSamples = 160;    // 10 ms per I2S read
// 32-bit I2S word -> 16-bit PCM. 16 = no gain; every step lower = +6 dB.
// Use the console "level" command to check you aren't clipping.
constexpr int kMicShift = 14;
// Ignore audio for this long after start-up while the mic settles.
constexpr uint32_t kMicSettleMs = 300;

// Slices handed to the detector (250 ms). With an Edge Impulse model this is
// replaced by EI_CLASSIFIER_SLICE_SIZE (a quarter of the model window).
constexpr uint32_t kDefaultSliceSamples = kSampleRateHz / 4;

// Training clips sent to the PC in capture/rec mode.
constexpr uint32_t kClipSamples = kSampleRateHz;          // 1 s, the usual EI window
constexpr uint32_t kClipPrerollSamples = kSampleRateHz / 4;  // keep 250 ms before the onset

// Ignore detections while bursting and for this long after: the bridge and
// piezo can make audible clicks.
constexpr uint32_t kSelfNoiseGuardMs = 300;

// Name of the "bark" class in your Edge Impulse project.
#define BARK_LABEL "bark"

// ---------------------------------------------------------------------------
// Ultrasonic driver
// ---------------------------------------------------------------------------
constexpr uint32_t kPwmResolutionHz = 10 * 1000 * 1000;  // MCPWM tick = 100 ns
constexpr uint32_t kSweepStepMs = 2;                     // frequency update interval

// ---------------------------------------------------------------------------
// Serial
// ---------------------------------------------------------------------------
// Fast enough to stream 16 kHz audio clips to the PC (needs ~320 kbaud).
constexpr uint32_t kSerialBaud = 921600;
constexpr uint32_t kStatusIntervalMs = 60000;  // periodic STAT line
