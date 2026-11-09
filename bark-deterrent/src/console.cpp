#include "console.h"

#include <Arduino.h>

#include <cstdarg>
#include <cstdio>

#include "config.h"

namespace {

SemaphoreHandle_t s_lock = nullptr;
char s_line[128];
size_t s_line_len = 0;

class Lock {
 public:
  Lock() { xSemaphoreTake(s_lock, portMAX_DELAY); }
  ~Lock() { xSemaphoreGive(s_lock); }
};

}  // namespace

void consoleBegin(uint32_t baud) {
  s_lock = xSemaphoreCreateMutex();
  Serial.begin(baud);
}

void logLine(const char* fmt, ...) {
  char buf[256];
  va_list args;
  va_start(args, fmt);
  vsnprintf(buf, sizeof(buf), fmt, args);
  va_end(args);
  Lock lock;
  Serial.println(buf);
}

void logEvent(const char* kind, float score, uint16_t bursts_last_hour) {
  logLine("EVT,%lu,%s,%.3f,%u", static_cast<unsigned long>(millis()), kind, score, bursts_last_hour);
}

void consoleSendClip(const int16_t* pcm, size_t samples, uint32_t t_ms, const char* reason) {
  char header[80];
  snprintf(header, sizeof(header), "CLIP,%lu,%u,%u,%s", static_cast<unsigned long>(t_ms),
           static_cast<unsigned>(samples), static_cast<unsigned>(kSampleRateHz), reason);
  Lock lock;  // nothing else may print in the middle of the binary payload
  Serial.println(header);
  // ESP32-S3 is little-endian, which is what the PC side expects.
  Serial.write(reinterpret_cast<const uint8_t*>(pcm), samples * sizeof(int16_t));
  Serial.println();
}

bool consoleReadLine(char* buf, size_t len) {
  while (Serial.available() > 0) {
    const int c = Serial.read();
    if (c < 0) break;
    if (c == '\r') continue;
    if (c == '\n') {
      s_line[s_line_len] = '\0';
      snprintf(buf, len, "%s", s_line);
      s_line_len = 0;
      return true;
    }
    if (s_line_len < sizeof(s_line) - 1) s_line[s_line_len++] = static_cast<char>(c);
  }
  return false;
}
