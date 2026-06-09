#pragma once
#include "IMachine.h"

// A no-hardware IMachine so the protocol + state machine can run on the Mega
// and talk to the GUI BEFORE any driver exists (comms-first). Moves and homing
// succeed instantly, sensors read safe defaults. Replace with the real Machine
// (TMC2209 / ToF / servo / relay) axis by axis during bench bring-up.

namespace pnp {

class StubMachine : public IMachine {
 public:
  Position getPosition() override { return pos_; }

  OpResult moveTo(float x, float y, float z, uint8_t = 80) override {
    pos_ = {x, y, z};
    return OpResult::Ok;
  }
  OpResult probeZ(float x, float y, float, float, float, float, float& outZ) override {
    outZ = 0.0f;
    pos_ = {x, y, 0.0f};
    return OpResult::Ok;
  }
  OpResult home(const char*) override { pos_ = {0, 0, 0}; return OpResult::Ok; }
  void     setOutput(const char*, bool) override {}
  bool     readSensor(const char*) override { return false; }
  OpResult delayMs(uint32_t) override { return OpResult::Ok; }
  void     log(const char*) override {}

 private:
  Position pos_{0, 0, 0};
};

}  // namespace pnp
