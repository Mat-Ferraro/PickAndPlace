#pragma once
#include <stdint.h>

namespace pnp {

enum class OpResult : uint8_t { Ok = 0, Aborted, Faulted };
struct Position { float x, y, z; };

class IMachine {
 public:
  virtual ~IMachine() {}
  virtual Position getPosition() = 0;
  virtual OpResult moveTo(float x, float y, float z, uint8_t speedPct = 80) = 0;
  virtual OpResult probeZ(float x, float y, float approachZ, float stepMm,
                          float maxDepthMm, float thresholdMm, float& outZ) = 0;
  virtual OpResult home(const char* axes) = 0;
  virtual void     setOutput(const char* name, bool value) = 0;
  virtual bool     readSensor(const char* name) = 0;
  virtual OpResult delayMs(uint32_t ms) = 0;
  virtual void     log(const char* msg) = 0;
  // Optional: live ToF confidence gates. Default no-op so stubs need not care.
  virtual void     setTofThresholds(uint16_t /*maxSigmaMm*/, uint16_t /*minSignalKcps*/) {}

  // Jog one motor by a raw, uncalibrated step count (signed: + / - chooses
  // direction). Used by jog-and-measure calibration — it deliberately bypasses
  // steps/mm because that is the value being calibrated. The operator jogs a
  // known number of steps, measures the physical distance moved, and the
  // firmware computes steps/mm from the two.
  // axis: "X", "Y1", "Y2", or "Z"  (Y1 = Y socket, Y2 = E0 socket)
  virtual OpResult jogAxisSteps(const char* axis, int32_t steps) = 0;

  // Read raw ToF distance for one arm pickup channel (0-3) in mm.
  virtual OpResult readDistanceMm(uint8_t channel, float& outMm) = 0;
};

}  // namespace pnp