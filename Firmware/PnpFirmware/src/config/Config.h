#pragma once
#include <stdint.h>
#include <stddef.h>

// EEPROM-backed machine configuration.
//
// Covers all values that must survive a power cycle:
//   - steps_per_mm per axis (written by set_cal_distance)
//   - servo open/closed angles
//   - probe tuning parameters
//
// Layout: plain struct, CRC16 over all bytes preceding the crc field.
// EEPROM I/O is Arduino-specific (EEPROM.h) and guarded by #ifdef ARDUINO.
// On the host the same struct is read/written against a static byte array,
// so the CRC, version-check, and round-trip logic are fully host-testable.
//
// Usage (Arduino):
//   static Config config;
//   config.load();           // at boot — populates fields from EEPROM or defaults
//   // ... after calibration ...
//   config.stepsPerMmX = 80.0f;
//   config.save();           // persists to EEPROM
//
// Usage (host tests):
//   Config::clearTestEeprom();   // wipe the fake EEPROM between tests
//   Config cfg;
//   cfg.save();  cfg.load();     // exercise round-trip

namespace pnp {

struct Config {
    // ---- schema ----
    static constexpr uint8_t  kVersion    = 1;
    static constexpr uint16_t kEepromAddr = 0;

    // ---- fields (in declaration order — CRC covers all of these) ----
    uint8_t version        = kVersion;

    // Stepper calibration (steps/mm). 0 = uncalibrated.
    float   stepsPerMmX    = 0.0f;
    float   stepsPerMmY    = 0.0f;
    float   stepsPerMmZ    = 0.0f;

    // Servo angles (degrees).
    float   servoDoorOpen        = 90.0f;
    float   servoDoorClosed      =  0.0f;
    float   servoLaserBtnPress   = 45.0f;
    float   servoLaserBtnRelease =  0.0f;

    // Probe tuning (mirrors interpreter.py config block defaults).
    float   probeStepMm      =   0.5f;
    float   probeMaxDepthMm  = 200.0f;
    float   probeThreshMm    =  40.0f;

    // ---- CRC (must be last field) ----
    uint16_t crc = 0;

    // ---- API ----

    // Load from EEPROM. Returns true if CRC and version are valid.
    // On failure (first boot, corruption) fields are reset to defaults.
    bool load();

    // Compute CRC, write struct to EEPROM.
    void save();

    // True if all three axes have been calibrated (steps/mm > 0).
    bool isCalibrated() const {
        return stepsPerMmX > 0.0f && stepsPerMmY > 0.0f && stepsPerMmZ > 0.0f;
    }

    // True if stored CRC matches the computed CRC and version is current.
    bool isValid() const;

    // Helpers
    static uint16_t computeCrc(const uint8_t* data, size_t len);
    void            updateCrc();

#ifndef ARDUINO
    // Wipe the fake EEPROM between host tests.
    static void clearTestEeprom();
    static constexpr size_t kFakeEepromSize = 64;
#endif
};

}  // namespace pnp
