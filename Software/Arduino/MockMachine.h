#pragma once
#include <stdint.h>
#include <string>
#include <vector>
#include "hal/IMachine.h"

// Host-only recording double — the C++ twin of conftest.py's FakeMachine.
// Every operation is instantaneous and recorded so tests can assert on the
// exact call sequence. Uses std::vector/std::string freely because it never
// runs on the MCU — it exists only in the host test build.

namespace pnp {

class MockMachine : public IMachine {
 public:
  // ---- scriptable ----
  Position position{0, 0, 0};
  float    probeResult = 0.0f;

  // ---- recordings (in call order) ----
  struct Move   { float x, y, z; uint8_t speed; };
  struct Output { std::string name; bool value; };
  std::vector<Move>        moves;
  std::vector<std::string> homes;
  std::vector<Output>      outputs;
  std::vector<std::string> reads;
  std::vector<uint32_t>    delays;
  std::vector<std::string> logs;

  // ---- IMachine ----
  Position getPosition() override { return position; }

  OpResult moveTo(float x, float y, float z, uint8_t speedPct = 80) override {
    moves.push_back({x, y, z, speedPct});
    position = {x, y, z};
    return OpResult::Ok;
  }

  OpResult probeZ(float x, float y, float, float, float, float, float& outZ) override {
    position = {x, y, probeResult};
    outZ = probeResult;
    return OpResult::Ok;
  }

  OpResult home(const char* axes) override {
    homes.push_back(axes);
    return OpResult::Ok;
  }

  void setOutput(const char* name, bool value) override {
    outputs.push_back({name, value});
  }

  bool readSensor(const char* name) override {
    reads.push_back(name);
    return false;
  }

  OpResult delayMs(uint32_t ms) override {
    delays.push_back(ms);
    return OpResult::Ok;
  }

  void log(const char* msg) override { logs.push_back(msg); }
};

}  // namespace pnp
