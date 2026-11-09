#include "settings_store.h"

#include <Preferences.h>

namespace {

constexpr const char* kNamespace = "barkdet";
constexpr const char* kKeyBlob = "settings";
constexpr const char* kKeyVersion = "version";
// Bump when the Settings struct changes so old saved data is ignored.
constexpr uint16_t kVersion = 1;

}  // namespace

bark::Settings settingsLoad() {
  Preferences prefs;
  bark::Settings s;
  // Read-write open: a read-only open logs an error on first boot (no namespace yet).
  if (prefs.begin(kNamespace, false)) {
    if (prefs.getUShort(kKeyVersion, 0) == kVersion && prefs.getBytesLength(kKeyBlob) == sizeof(s)) {
      prefs.getBytes(kKeyBlob, &s, sizeof(s));
    }
    prefs.end();
  }
  return bark::sanitize(s);
}

void settingsSave(const bark::Settings& s) {
  Preferences prefs;
  if (!prefs.begin(kNamespace, false)) return;
  prefs.putUShort(kKeyVersion, kVersion);
  prefs.putBytes(kKeyBlob, &s, sizeof(s));
  prefs.end();
}

void settingsErase() {
  Preferences prefs;
  if (!prefs.begin(kNamespace, false)) return;
  prefs.clear();
  prefs.end();
}
