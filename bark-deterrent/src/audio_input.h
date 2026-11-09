// INMP441 I2S microphone input.
#pragma once

#include <cstddef>
#include <cstdint>

// Sets up the I2S peripheral. Returns false if the driver fails to start.
bool audioBegin();

// Blocks until `count` samples (16-bit PCM, DC removed) have been read.
// Returns the number of samples written to `out` (0 on a read error).
size_t audioRead(int16_t* out, size_t count);
