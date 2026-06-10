#pragma once
#include <stdint.h>
#include <string>
#include <vector>
#include <map>
#include "hal/IMachine.h"

// Host-only recording double — the C++ twin of conftest.py's FakeMachine.
// Extended with probe recording and sensor-flip support for the interpreter
// tests (mirrors the on_read_sensor hook in FakeMachine).

namespace pnp {

class MockMachine : public IMachine {
 public:
    // ---- scriptable ----
    Position position{0, 0, 0};
    float    probeResult = 0.0f;
    std::map<std::string, bool> sensors;
    int sensorFlipAfter  = -1;   // flip sensor to true after N reads (-1 = never)
    int sensorReadCount  = 0;

    // ---- recordings ----
    struct Move  { float x, y, z; uint8_t speed; };
    struct Probe { float x, y, approachZ, step, maxDepth, threshold; };
    struct Output { std::string name; bool value; };

    std::vector<Move>        moves;
    std::vector<Probe>       probes;
    std::vector<std::string> homes;
    std::vector<Output>      outputs;
    std::vector<uint32_t>    delays;
    std::vector<std::string> logs;
    std::vector<std::string> reads;

    // ---- IMachine ----
    Position getPosition() override { return position; }

    OpResult moveTo(float x, float y, float z, uint8_t speed = 80) override {
        moves.push_back({x, y, z, speed});
        position = {x, y, z};
        return OpResult::Ok;
    }

    OpResult probeZ(float x, float y, float approachZ,
                    float step, float maxDepth, float threshold,
                    float& outZ) override {
        probes.push_back({x, y, approachZ, step, maxDepth, threshold});
        outZ = probeResult;
        position = {x, y, probeResult};
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
        sensorReadCount++;
        if (sensorFlipAfter >= 0 && sensorReadCount > sensorFlipAfter) {
            return true;
        }
        auto it = sensors.find(name);
        return it != sensors.end() ? it->second : false;
    }

    OpResult delayMs(uint32_t ms) override {
        delays.push_back(ms);
        return OpResult::Ok;
    }

    void log(const char* msg) override { logs.push_back(msg); }
};

}  // namespace pnp
