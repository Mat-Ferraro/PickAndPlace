#pragma once
#include "IMachine.h"

namespace pnp {

class StubMachine : public IMachine {
 public:
  Position getPosition() override { return pos_; }
  OpResult moveTo(float x, float y, float z, uint8_t = 80) override {
    pos_ = {x,y,z}; return OpResult::Ok;
  }
  OpResult probeZ(float x, float y, float, float, float, float, float& outZ) override {
    outZ = 0.0f; pos_ = {x,y,0.0f}; return OpResult::Ok;
  }
  OpResult home(const char*) override { pos_ = {0,0,0}; return OpResult::Ok; }
  void     setOutput(const char*, bool) override {}
  bool     readSensor(const char*) override { return false; }
  OpResult delayMs(uint32_t) override { return OpResult::Ok; }
  void     log(const char*) override {}
  // Returns axis-appropriate fake step count for calibration testing.
  OpResult traverseToStop(const char* axis, uint32_t& outSteps) override {
    // Distinct values per axis so tests can verify routing.
    if (axis[0]=='X')                   outSteps = 12800;
    else if (axis[0]=='Y'&&axis[1]=='1') outSteps = 12800;
    else if (axis[0]=='Y'&&axis[1]=='2') outSteps = 12750;  // slight difference
    else                                 outSteps =  6400;  // Z
    return OpResult::Ok;
  }
  OpResult readDistanceMm(uint8_t, float& outMm) override {
    outMm = 50.0f; return OpResult::Ok;
  }
 private:
  Position pos_{0,0,0};
};

}  // namespace pnp
