#pragma once
#include <stdint.h>

namespace pnp {

enum class OpResult : uint8_t {
  Ok = 0,
  Aborted,   // E-stop / pause asserted mid-operation
  Faulted    // hardware fault (stall, sensor timeout, out of range, ...)
};

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

  // Drive axis to its far hard stop using StallGuard, counting steps.
  // axis: 'X', 'Y', or 'Z'
  // outSteps: raw step count of the full travel (used to compute steps/mm).
  virtual OpResult traverseToStop(char axis, uint32_t& outSteps) = 0;
};

}  // namespace pnp
