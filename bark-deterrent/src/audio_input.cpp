#include "audio_input.h"

#include <driver/i2s_std.h>
#include <freertos/FreeRTOS.h>

#include "config.h"
#include "pcm.h"

namespace {

i2s_chan_handle_t s_rx = nullptr;
bark::DcBlocker s_dc;
int32_t s_raw[kChunkSamples];

}  // namespace

bool audioBegin() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  if (i2s_new_channel(&chan_cfg, nullptr, &s_rx) != ESP_OK) return false;

  // The INMP441 sends 24-bit data in 32-bit slots; with L/R tied to GND it
  // talks in the left slot.
  i2s_std_config_t std_cfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kSampleRateHz),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
      .gpio_cfg =
          {
              .mclk = I2S_GPIO_UNUSED,
              .bclk = static_cast<gpio_num_t>(kPinMicBclk),
              .ws = static_cast<gpio_num_t>(kPinMicWs),
              .dout = I2S_GPIO_UNUSED,
              .din = static_cast<gpio_num_t>(kPinMicData),
              .invert_flags =
                  {
                      .mclk_inv = false,
                      .bclk_inv = false,
                      .ws_inv = false,
                  },
          },
  };
  std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

  if (i2s_channel_init_std_mode(s_rx, &std_cfg) != ESP_OK) return false;
  return i2s_channel_enable(s_rx) == ESP_OK;
}

size_t audioRead(int16_t* out, size_t count) {
  size_t done = 0;
  while (done < count) {
    const size_t want = (count - done) < kChunkSamples ? (count - done) : kChunkSamples;
    size_t bytes = 0;
    if (i2s_channel_read(s_rx, s_raw, want * sizeof(int32_t), &bytes, portMAX_DELAY) != ESP_OK) {
      return 0;
    }
    const size_t got = bytes / sizeof(int32_t);
    for (size_t i = 0; i < got; ++i) {
      out[done + i] = bark::saturateToInt16(s_dc.process(bark::i2sWordToFloat(s_raw[i], kMicShift)));
    }
    done += got;
  }
  return done;
}
