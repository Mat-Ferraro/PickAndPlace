#pragma once
#include <stdint.h>
#include <stddef.h>
#include "TravelLimits.h"

// EEPROM-backed machine configuration.
//
// Schema v4: added per-axis maxTravelMm (soft travel limits / work envelope).
//   With limit-switch homing the home switch defines position 0 for each axis,
//   so the firmware needs the axis length to bound motion: a MOVE outside
//   [0, maxTravelMm] faults at runtime. The operator enters these via the GUI
//   and they persist here, so the machine can home and run headless with no GUI
//   attached. This replaces StallGuard's former role of protecting the far end
//   of travel.
//
// Schema v3: stepsPerMmY split into stepsPerMmY1/stepsPerMmY2 for independent
//   calibration of the dual-Y gantry motors (Y1 on Y socket, Y2 on E0 socket).
//
// Why the Y fields are asymmetric (per-motor steps/mm, single Y travel):
//   - steps/mm is a per-MOTOR electromechanical property. Y1 and Y2 may differ
//     slightly and can be driven independently for anti-racking, so each motor
//     stores its own value.
//   - maxTravel is a per-AXIS geometric property. The dual-Y gantry has one
//     physical Y travel envelope regardless of motor count, so Y stores one.

namespace pnp {

struct Config {
    static constexpr uint8_t  kVersion    = 4;
    static constexpr uint16_t kEepromAddr = 0;

    uint8_t version = kVersion;

    // Stepper calibration (steps/mm), per motor. 0 = uncalibrated.
    float stepsPerMmX  = 0.0f;
    float stepsPerMmY1 = 0.0f;   // Y axis, motor 1 (Y socket,  D60/61/56)
    float stepsPerMmY2 = 0.0f;   // Y axis, motor 2 (E0 socket, D26/28/24)
    float stepsPerMmZ  = 0.0f;

    // Soft travel limits (mm), per axis. 0 = not configured.
    // Usable envelope after homing is [0, maxTravelMm*]. Operator-entered via GUI.
    float maxTravelMmX = 0.0f;
    float maxTravelMmY = 0.0f;   // one envelope for the dual-Y gantry
    float maxTravelMmZ = 0.0f;

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

    // True when all four drive motors are calibrated.
    bool isCalibrated() const {
        return stepsPerMmX  > 0.0f && stepsPerMmY1 > 0.0f &&
               stepsPerMmY2 > 0.0f && stepsPerMmZ  > 0.0f;
    }
    // True when every axis has a soft travel limit configured.
    bool hasTravelLimits() const {
        return maxTravelMmX > 0.0f && maxTravelMmY > 0.0f && maxTravelMmZ > 0.0f;
    }
    // Ready for unattended (headless) motion: calibrated AND bounded.
    bool isReadyForMotion() const {
        return isCalibrated() && hasTravelLimits();
    }
    // Snapshot of the soft travel envelope for the motion path. enabled tracks
    // hasTravelLimits(), so an unconfigured machine yields an unbounded (no-op)
    // guard rather than faulting every move.
    TravelLimits travelLimits() const {
        TravelLimits t;
        t.maxX    = maxTravelMmX;
        t.maxY    = maxTravelMmY;
        t.maxZ    = maxTravelMmZ;
        t.enabled = hasTravelLimits();
        return t;
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