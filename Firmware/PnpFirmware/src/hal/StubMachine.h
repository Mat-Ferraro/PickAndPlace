#pragma once
#include "IMachine.h"

// No-hardware IMachine for comms-first bring-up.
// traverseToStop returns a plausible fake step count so calibration
// state transitions can be exercised without hardware.

namespace pnp {

class StubMachine : public IMachine {
 public:
  Position getPosition() override { return pos_; }

  OpResult moveTo(float x, float y, float z, uint8_t = 80) override {
    pos_ = {x, y, z}; return OpResult::Ok;
  }
  OpResult probeZ(float x, float y, float, float, float, float,
                  float& outZ) override {
    outZ = 0.0f; pos_ = {x, y, 0.0f}; return OpResult::Ok;
  }
  OpResult home(const char*) override { pos_ = {0,0,0}; return OpResult::Ok; }
  void     setOutput(const char*, bool) override {}
  bool     readSensor(const char*) override { return false; }
  OpResult delayMs(uint32_t) override { return OpResult::Ok; }
  void     log(const char*) override {}

  // Returns 3200 steps as a placeholder — enough to exercise the
  // calibration flow without real hardware.
  OpResult traverseToStop(char, uint32_t& outSteps) override {
    outSteps = 3200;
    return OpResult::Ok;
  }

 private:
  Position pos_{0, 0, 0};
};

}  // namespace pnp
