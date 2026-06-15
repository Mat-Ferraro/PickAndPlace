#pragma once
#include <stdint.h>
#include <stddef.h>

// EEPROM-backed machine configuration.
// Schema v3: stepsPerMmY split into stepsPerMmY1 and stepsPerMmY2 to support
// independent calibration of the dual-Y gantry motors (Y1 on Y socket,
// Y2 on E0 socket). Both may differ slightly due to belt tension and assembly
// tolerances; independent values avoid accumulated squareness error.

namespace pnp {

struct Config {
    static constexpr uint8_t  kVersion    = 3;
    static constexpr uint16_t kEepromAddr = 0;

    uint8_t version = kVersion;

    // Stepper calibration (steps/mm). 0 = uncalibrated.
    float stepsPerMmX  = 0.0f;
    float stepsPerMmY1 = 0.0f;   // Y-axis, motor 1 (Y socket,  D60/61/56)
    float stepsPerMmY2 = 0.0f;   // Y-axis, motor 2 (E0 socket, D26/28/24)
    float stepsPerMmZ  = 0.0f;

    // ToF sensor offset calibration (mm). Arm pickup channels ch0-ch3.
    // -1.0 = not yet calibrated.
    float tofOffsetMm[4] = {-1.0f, -1.0f, -1.0f, -1.0f};

    // Servo angles (degrees).
    float servoDoorOpen        = 90.0f;
    float servoDoorClosed      =  0.0f;
    float servoLaserBtnPress   = 45.0f;
    float servoLaserBtnRelease =  0.0f;

    // Probe tuning.
    float probeStepMm     =   0.5f;
    float probeMaxDepthMm = 200.0f;
    float probeThreshMm   =  40.0f;

    // CRC must be last.
    uint16_t crc = 0;

    bool load();
    void save();

    // True when all four drive axes are calibrated.
    bool isCalibrated() const {
        return stepsPerMmX  > 0.0f && stepsPerMmY1 > 0.0f &&
               stepsPerMmY2 > 0.0f && stepsPerMmZ  > 0.0f;
    }
    bool isSensorCalibrated(uint8_t ch) const {
        return ch < 4 && tofOffsetMm[ch] >= 0.0f;
    }
    bool isValid() const;
    static uint16_t computeCrc(const uint8_t* data, size_t len);
    void            updateCrc();

#ifndef ARDUINO
    static void clearTestEeprom();
    static constexpr size_t kFakeEepromSize = 128;
#endif
};

}  // namespace pnp
