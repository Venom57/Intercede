// Serial output shared by all tasks, plus the line-based command input.
//
// Line formats (one per line, so they're easy to grep or log to a file):
//   I <text>                                  info
//   E <text>                                  error
//   EVT,<uptime_ms>,<kind>,<score>,<bursts_last_hour>
//   STAT,<uptime_ms>,<key>=<value>,...
//   CLIP,<uptime_ms>,<samples>,<rate_hz>,<reason>   followed by exactly
//        <samples>*2 bytes of raw 16-bit little-endian PCM (tools/capture_clips.py)
#pragma once

#include <cstddef>
#include <cstdint>

void consoleBegin(uint32_t baud);

void logLine(const char* fmt, ...) __attribute__((format(printf, 1, 2)));

void logEvent(const char* kind, float score, uint16_t bursts_last_hour);

void consoleSendClip(const int16_t* pcm, size_t samples, uint32_t t_ms, const char* reason);

// Collects characters until a newline. Returns true (and fills `buf`) when a
// whole line is ready. Never blocks.
bool consoleReadLine(char* buf, size_t len);
