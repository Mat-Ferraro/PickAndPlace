#pragma once
#include <stdint.h>

// Portable: no Arduino.h, no dynamic allocation. Compiles for AVR and host.

namespace pnp {

// The seven machine states. Mirrors simulator.py's State enum exactly, which
// is the behavioural spec the firmware ports.
enum class State : uint8_t {
  Idle = 0,
  Homing,
  Ready,
  Running,
  Paused,
  Faulted,
  Estopped,
  Count
};

inline const char* stateName(State s) {
  switch (s) {
    case State::Idle:     return "IDLE";
    case State::Homing:   return "HOMING";
    case State::Ready:    return "READY";
    case State::Running:  return "RUNNING";
    case State::Paused:   return "PAUSED";
    case State::Faulted:  return "FAULTED";
    case State::Estopped: return "ESTOPPED";
    default:              return "?";
  }
}

// One-hot bit for a state, so command gating is a cheap mask test.
constexpr uint8_t stbit(State s) { return uint8_t(1u << uint8_t(s)); }

}  // namespace pnp
