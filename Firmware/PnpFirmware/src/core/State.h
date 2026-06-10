#pragma once
#include <stdint.h>

namespace pnp {

enum class State : uint8_t {
  Idle = 0,
  Homing,
  Ready,
  Running,
  Paused,
  Faulted,
  Estopped,
  Calibrating,   // traversing to far stop, then awaiting set_cal_distance
  Count
};

inline const char* stateName(State s) {
  switch (s) {
    case State::Idle:        return "IDLE";
    case State::Homing:      return "HOMING";
    case State::Ready:       return "READY";
    case State::Running:     return "RUNNING";
    case State::Paused:      return "PAUSED";
    case State::Faulted:     return "FAULTED";
    case State::Estopped:    return "ESTOPPED";
    case State::Calibrating: return "CALIBRATING";
    default:                 return "?";
  }
}

// One-hot bit for command gating. 8 states = all fit in uint8_t.
constexpr uint8_t stbit(State s) { return uint8_t(1u << uint8_t(s)); }

}  // namespace pnp
