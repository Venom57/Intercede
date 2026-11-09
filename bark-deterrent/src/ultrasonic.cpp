#include "ultrasonic.h"

#include <Arduino.h>
#include <driver/mcpwm_prelude.h>
#include <esp_timer.h>

#include <atomic>

#include "config.h"
#include "settings.h"
#include "sweep.h"

namespace {

mcpwm_timer_handle_t s_timer = nullptr;
mcpwm_oper_handle_t s_oper = nullptr;
mcpwm_cmpr_handle_t s_cmpr = nullptr;
mcpwm_gen_handle_t s_gen_a = nullptr;  // -> IN1
mcpwm_gen_handle_t s_gen_b = nullptr;  // -> IN2

TaskHandle_t s_task = nullptr;
BurstParams s_pending{};
std::atomic<bool> s_active{false};
std::atomic<uint32_t> s_last_end_ms{0};

uint64_t nowMs() { return static_cast<uint64_t>(esp_timer_get_time() / 1000); }

void setFrequency(uint32_t hz) {
  const uint32_t ticks = bark::periodTicksForHz(kPwmResolutionHz, bark::clampUltrasonicHz(hz));
  // Both updates take effect at the next period boundary (timer empty), so
  // the duty cycle stays at 50% while the frequency moves.
  mcpwm_timer_set_period(s_timer, ticks);
  mcpwm_comparator_set_compare_value(s_cmpr, ticks / 2);
}

// Both bridge inputs low = no drive across the piezo.
void outputsOff() {
  mcpwm_generator_set_force_level(s_gen_a, 0, true);
  mcpwm_generator_set_force_level(s_gen_b, 0, true);
}

void outputsOn() {
  mcpwm_generator_set_force_level(s_gen_a, -1, true);
  mcpwm_generator_set_force_level(s_gen_b, -1, true);
}

void burstTask(void*) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    const BurstParams p = s_pending;

    const uint64_t start = nowMs();
    setFrequency(p.freq_lo_hz);
    outputsOn();
    for (uint64_t t = 0; t < p.duration_ms; t = nowMs() - start) {
      setFrequency(bark::sweepFrequencyHz(static_cast<uint32_t>(t), p.freq_lo_hz, p.freq_hi_hz,
                                          p.sweep_period_ms));
      vTaskDelay(pdMS_TO_TICKS(kSweepStepMs) > 0 ? pdMS_TO_TICKS(kSweepStepMs) : 1);
    }
    outputsOff();

    s_last_end_ms.store(static_cast<uint32_t>(nowMs()));
    s_active.store(false);
  }
}

bool check(esp_err_t err) { return err == ESP_OK; }

// (Built by hand: the IDF helper macros use C compound literals.)
mcpwm_gen_timer_event_action_t onTimerEmpty(mcpwm_generator_action_t action) {
  mcpwm_gen_timer_event_action_t a = {};
  a.direction = MCPWM_TIMER_DIRECTION_UP;
  a.event = MCPWM_TIMER_EVENT_EMPTY;
  a.action = action;
  return a;
}

mcpwm_gen_compare_event_action_t onCompare(mcpwm_generator_action_t action) {
  mcpwm_gen_compare_event_action_t a = {};
  a.direction = MCPWM_TIMER_DIRECTION_UP;
  a.comparator = s_cmpr;
  a.action = action;
  return a;
}

}  // namespace

bool ultrasonicBegin() {
  const uint32_t start_ticks = bark::periodTicksForHz(kPwmResolutionHz, bark::kUltrasonicMinHz);

  mcpwm_timer_config_t timer_cfg = {};
  timer_cfg.group_id = 0;
  timer_cfg.clk_src = MCPWM_TIMER_CLK_SRC_DEFAULT;
  timer_cfg.resolution_hz = kPwmResolutionHz;
  timer_cfg.count_mode = MCPWM_TIMER_COUNT_MODE_UP;
  timer_cfg.period_ticks = start_ticks;
  timer_cfg.flags.update_period_on_empty = 1;
  if (!check(mcpwm_new_timer(&timer_cfg, &s_timer))) return false;

  mcpwm_operator_config_t oper_cfg = {};
  oper_cfg.group_id = 0;
  if (!check(mcpwm_new_operator(&oper_cfg, &s_oper))) return false;
  if (!check(mcpwm_operator_connect_timer(s_oper, s_timer))) return false;

  mcpwm_comparator_config_t cmpr_cfg = {};
  cmpr_cfg.flags.update_cmp_on_tez = 1;
  if (!check(mcpwm_new_comparator(s_oper, &cmpr_cfg, &s_cmpr))) return false;
  if (!check(mcpwm_comparator_set_compare_value(s_cmpr, start_ticks / 2))) return false;

  mcpwm_generator_config_t gen_cfg = {};
  gen_cfg.gen_gpio_num = kPinBridgeIn1;
  if (!check(mcpwm_new_generator(s_oper, &gen_cfg, &s_gen_a))) return false;
  gen_cfg.gen_gpio_num = kPinBridgeIn2;
  if (!check(mcpwm_new_generator(s_oper, &gen_cfg, &s_gen_b))) return false;

  // Hold both inputs low before the timer ever runs.
  outputsOff();

  // IN1: high for the first half of each period, low for the second.
  // IN2: the opposite. The bridge sees a full +V / -V square wave.
  // Integrated bridge drivers (DRV8871 etc.) add their own dead time; if you
  // build a bridge from discrete MOSFETs, add dead time with
  // mcpwm_generator_set_dead_time().
  if (!check(mcpwm_generator_set_action_on_timer_event(s_gen_a, onTimerEmpty(MCPWM_GEN_ACTION_HIGH)))) return false;
  if (!check(mcpwm_generator_set_action_on_compare_event(s_gen_a, onCompare(MCPWM_GEN_ACTION_LOW)))) return false;
  if (!check(mcpwm_generator_set_action_on_timer_event(s_gen_b, onTimerEmpty(MCPWM_GEN_ACTION_LOW)))) return false;
  if (!check(mcpwm_generator_set_action_on_compare_event(s_gen_b, onCompare(MCPWM_GEN_ACTION_HIGH)))) return false;

  if (!check(mcpwm_timer_enable(s_timer))) return false;
  if (!check(mcpwm_timer_start_stop(s_timer, MCPWM_TIMER_START_NO_STOP))) return false;

  return xTaskCreatePinnedToCore(burstTask, "burst", 3072, nullptr, 8, &s_task, 0) == pdPASS;
}

bool ultrasonicBurst(const BurstParams& params) {
  if (s_task == nullptr) return false;
  bool expected = false;
  if (!s_active.compare_exchange_strong(expected, true)) return false;

  BurstParams p = params;
  if (p.duration_ms > bark::limits::kBurstMaxMs) p.duration_ms = bark::limits::kBurstMaxMs;
  p.freq_lo_hz = bark::clampUltrasonicHz(p.freq_lo_hz);
  p.freq_hi_hz = bark::clampUltrasonicHz(p.freq_hi_hz);
  if (p.freq_hi_hz < p.freq_lo_hz) p.freq_hi_hz = p.freq_lo_hz;
  s_pending = p;

  xTaskNotifyGive(s_task);
  return true;
}

bool ultrasonicActive() { return s_active.load(); }

uint32_t ultrasonicLastEndMs() { return s_last_end_ms.load(); }
