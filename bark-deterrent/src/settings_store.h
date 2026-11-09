// Saves settings to flash (NVS) so they survive a reboot.
#pragma once

#include "settings.h"

// Loads saved settings, or defaults if none are saved or the format changed.
bark::Settings settingsLoad();
void settingsSave(const bark::Settings& s);
void settingsErase();
