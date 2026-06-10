#pragma once
#include <stdint.h>

// The hardware abstraction seam. Every layer above this (state machine,
// interpreter, protocol) depends ONLY on this interface, never on Arduino APIs
// directly. That is what lets the logic compile and run under host unit tests
// against a MockMachine, exactly like the Python FakeMachine in conftest.py.
//
// Ported from interpreter.py's MachineInterface. The Python version raises
// ProgramFault and checks a threading.Event for abort; on AVR we avoid
// exceptions, so abortable operations return an OpResult instead and the
// concrete Machine checks the global abort/E-stop flag internally.

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

  virtual OpResult home(const char* axes) = 0;   // axes e.g. "XYZ", "Z"

  virtual void     setOutput(const char* name, bool value) = 0;   // "pump"/"valve"
  virtual bool     readSensor(const char* name) = 0;
  virtual OpResult delayMs(uint32_t ms) = 0;
  virtual void     log(const char* msg) = 0;
};

}  // namespace pnp
