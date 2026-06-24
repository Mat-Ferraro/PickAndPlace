#pragma once
#include <stdint.h>
#include <stddef.h>
#include "TravelLimits.h"

// EEPROM-backed machine configuration.
//
// Schema v6: added persisted ToF confidence gates (tofMaxSigmaMm,
// tofMinSignalKcps) so GUI-tuned values survive power cycles and apply at boot.
//
// Schema v5: collapsed the dual-Y steps/mm back to a SINGLE stepsPerMmY.
//   The two Y motors are bolted to one rigid gantry beam, so they are
//   mechanically forced to advance identically — they cannot have different
//   steps/mm without racking or binding the beam. steps/mm is therefore a
//   per-AXIS property for Y, exactly like X and Z. The motors are still driven
//   independently for ONE purpose only: homing/squaring each side to its own
//   limit switch. After squaring they always move in lockstep, so calibration
//   and motion use the single stepsPerMmY value. (v3 had split Y1/Y2 steps/mm;
//   that distinction was never physically meaningful and is removed here.)
//
// Schema v4: added per-axis maxTravelMm (soft travel limits / work envelope).
//   With limit-switch homing the home switch defines position 0 for each axis,
//   so the firmware needs the axis length to bound motion: a MOVE outside
//   [0, maxTravelMm] faults at runtime. The operator enters these via the GUI
//   and they persist here, so the machine can home and run headless with no GUI
//   attached.

namespace pnp {

struct Config {
    static constexpr uint8_t  kVersion    = 6;
    static constexpr uint16_t kEepromAddr = 0;

    uint8_t version = kVersion;

    // Stepper calibration (steps/mm), per AXIS. 0 = uncalibrated.
    // Y is a single value shared by both gantry motors (they move in lockstep).
    float stepsPerMmX = 0.0f;
    float stepsPerMmY = 0.0f;   // dual-Y gantry: both motors, one value
    float stepsPerMmZ = 0.0f;

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

    // ToF confidence gates (live-tunable from the GUI; persisted here so tuned
    // values survive power cycles and are applied at boot — needed for headless).
    // sigma = max measurement uncertainty in mm; signal = min return strength in
    // kcps (0 disables that gate).
    uint16_t tofMaxSigmaMm    = 15;
    uint16_t tofMinSignalKcps = 0;

    // CRC must be last.
    uint16_t crc = 0;

    bool load();
    void save();

    // True when all three drive axes are calibrated.
    bool isCalibrated() const {
        return stepsPerMmX > 0.0f && stepsPerMmY > 0.0f && stepsPerMmZ > 0.0f;
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
