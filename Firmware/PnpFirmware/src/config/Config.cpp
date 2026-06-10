#include "Config.h"
#include <string.h>

// EEPROM I/O is isolated here so the rest of the file compiles on the host.
#ifdef ARDUINO
  #include <EEPROM.h>
  #define CFG_EEPROM_GET(addr, val) EEPROM.get((addr), (val))
  #define CFG_EEPROM_PUT(addr, val) EEPROM.put((addr), (val))
#else
  #include <stdint.h>
  // Fake EEPROM for host tests — a static byte array.
  static uint8_t s_eeprom[pnp::Config::kFakeEepromSize] = {};
  template<typename T>
  static void eepromGet(int addr, T& t) {
      memcpy(&t, s_eeprom + addr, sizeof(T));
  }
  template<typename T>
  static void eepromPut(int addr, const T& t) {
      memcpy(s_eeprom + addr, &t, sizeof(T));
  }
  #define CFG_EEPROM_GET(addr, val) eepromGet((addr), (val))
  #define CFG_EEPROM_PUT(addr, val) eepromPut((addr), (val))
#endif

namespace pnp {

// ============================================================
// CRC16/CCITT — table-free, portable, deterministic
// ============================================================

uint16_t Config::computeCrc(const uint8_t* data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int bit = 0; bit < 8; bit++) {
            if (crc & 0x8000) crc = (uint16_t)((crc << 1) ^ 0x1021);
            else              crc = (uint16_t)(crc << 1);
        }
    }
    return crc;
}

void Config::updateCrc() {
    crc = computeCrc(reinterpret_cast<const uint8_t*>(this),
                     offsetof(Config, crc));
}

bool Config::isValid() const {
    if (version != kVersion) return false;
    uint16_t expected = computeCrc(reinterpret_cast<const uint8_t*>(this),
                                   offsetof(Config, crc));
    return crc == expected;
}

// ============================================================
// load / save
// ============================================================

bool Config::load() {
    Config stored;
    CFG_EEPROM_GET(kEepromAddr, stored);
    if (!stored.isValid()) {
        // First boot or corruption — keep defaults already in *this.
        return false;
    }
    *this = stored;
    return true;
}

void Config::save() {
    updateCrc();
    CFG_EEPROM_PUT(kEepromAddr, *this);
}

// ============================================================
// Host test helper
// ============================================================

#ifndef ARDUINO
void Config::clearTestEeprom() {
    memset(s_eeprom, 0, kFakeEepromSize);
}
#endif

}  // namespace pnp
