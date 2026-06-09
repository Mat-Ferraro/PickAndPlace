#include "StateMachine.h"
#include <string.h>

namespace pnp {

namespace {

// Command -> allowed-state mask. Ported verbatim from simulator.py
// COMMAND_STATES, so the firmware gates commands identically to the simulator
// the GUI already talks to.
struct CmdGate { const char* name; uint8_t allowed; };

const CmdGate kGates[] = {
  {"home",           uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"load_program",   uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"run_program",    stbit(State::Ready)},
  {"pause",          stbit(State::Running)},
  {"resume",         stbit(State::Paused)},
  {"reset_fault",    stbit(State::Faulted)},
  {"reset_estop",    stbit(State::Estopped)},
  {"jog",            stbit(State::Ready)},
  {"teach_position", stbit(State::Ready)},
  {"query_position", uint8_t(stbit(State::Idle) | stbit(State::Ready) |
                             stbit(State::Faulted) | stbit(State::Estopped))},
  {"move_to",        stbit(State::Ready)},
  {"save_position",  uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"set_param",      uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"save_config",    uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"load_config",    uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"begin_transfer", uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"program_chunk",  uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"end_transfer",   uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"query_sensors",  uint8_t(stbit(State::Idle) | stbit(State::Ready) |
                             stbit(State::Running) | stbit(State::Paused) |
                             stbit(State::Faulted) | stbit(State::Estopped))},
  {"set_output",     uint8_t(stbit(State::Idle) | stbit(State::Ready))},
  {"set_servo",      uint8_t(stbit(State::Idle) | stbit(State::Ready))},
};

// Accepted in any state (ported from simulator.py ALWAYS_ACCEPT).
const char* kAlwaysAccept[] = {
  "estop", "get_param", "query_status", "laser_safe", "query_positions", "get_program",
};

bool isAlwaysAccept(const char* name) {
  for (const char* a : kAlwaysAccept) {
    if (strcmp(a, name) == 0) return true;
  }
  return false;
}

const CmdGate* gateFor(const char* name) {
  for (const CmdGate& g : kGates) {
    if (strcmp(g.name, name) == 0) return &g;
  }
  return nullptr;
}

}  // namespace

Response StateMachine::ack(const Command& c) const {
  return Response{Response::Ack, c.id, c.name, ""};
}

Response StateMachine::nack(const Command& c, const char* reason) const {
  return Response{Response::Nack, c.id, c.name, reason};
}

void StateMachine::enterHoming(uint32_t nowMs) {
  // Record intent on the machine; the real HAL performs sensorless homing.
  machine_.home("XYZ");
  state_ = State::Homing;
  homingDeadline_ = nowMs + kHomingMs;
}

Response StateMachine::handleCommand(const Command& cmd, uint32_t nowMs) {
  const char* name = cmd.name;

  // E-stop is always accepted and dominates every other state.
  if (strcmp(name, "estop") == 0) {
    setEstopHardware(true);
    return ack(cmd);
  }

  if (isAlwaysAccept(name)) {
    // query_* / get_* : no state change in the scaffold; the protocol layer
    // attaches the queried payload (status, positions, params) to the reply.
    return ack(cmd);
  }

  const CmdGate* g = gateFor(name);
  if (!g) return nack(cmd, "unknown_command");

  if (!(g->allowed & stbit(state_))) {
    const char* reason = (state_ == State::Estopped) ? "estop_active"
                       : (state_ == State::Faulted)  ? "hw_fault"
                                                     : "not_ready";
    return nack(cmd, reason);
  }

  // ---- gated commands with state effects (scaffold subset) ----
  if (strcmp(name, "home") == 0) {
    enterHoming(nowMs);
    return ack(cmd);
  }
  if (strcmp(name, "run_program") == 0) {
    if (!programLoaded_) return nack(cmd, "no_program");
    state_ = State::Running;   // TODO: hand off to the interpreter
    return ack(cmd);
  }
  if (strcmp(name, "pause") == 0)  { state_ = State::Paused;  return ack(cmd); }
  if (strcmp(name, "resume") == 0) { state_ = State::Running; return ack(cmd); }
  if (strcmp(name, "reset_fault") == 0) {
    fault_ = nullptr;
    state_ = State::Idle;
    return ack(cmd);
  }
  if (strcmp(name, "reset_estop") == 0) {
    if (estopHw_) return nack(cmd, "hw_fault");   // latch still engaged
    fault_ = nullptr;
    state_ = State::Idle;
    return ack(cmd);
  }

  // Allowed in this state but not yet implemented (jog, move_to, set_output,
  // set_servo, transfer, config, teach/save). Acknowledged as a no-op for now.
  // TODO: wire to interpreter / config / HAL.
  return ack(cmd);
}

void StateMachine::tick(uint32_t nowMs) {
  // Signed compare so it survives millis() wraparound.
  if (state_ == State::Homing && (int32_t)(nowMs - homingDeadline_) >= 0) {
    state_ = State::Ready;   // TODO: real completion signalled by the HAL
  }
  // TODO: when RUNNING, step the interpreter here.
}

void StateMachine::pressButton(const char* button, uint32_t nowMs) {
  if (strcmp(button, "start") == 0) {            // "proceed"
    if (state_ == State::Idle) {
      enterHoming(nowMs);
    } else if (state_ == State::Ready) {
      if (programLoaded_) state_ = State::Running;   // else refused (would beep)
    } else if (state_ == State::Paused) {
      state_ = State::Running;
    }
  } else if (strcmp(button, "pause") == 0) {     // "halt / dismiss"
    if (state_ == State::Running) {
      state_ = State::Paused;
    } else if (state_ == State::Faulted) {
      fault_ = nullptr;
      state_ = State::Idle;
    }
  }
}

void StateMachine::setEstopHardware(bool active) {
  estopHw_ = active;
  if (active) {
    fault_ = "estop_triggered";
    state_ = State::Estopped;
  } else if (state_ == State::Estopped) {
    // Releasing the latch returns to IDLE (position trust lost -> must re-home).
    fault_ = nullptr;
    state_ = State::Idle;
  }
}

void StateMachine::injectFault(const char* reason) {
  if (state_ == State::Estopped) return;   // E-stop dominates
  fault_ = reason;
  state_ = State::Faulted;
}

StatusSnapshot StateMachine::buildStatus() const {
  return StatusSnapshot{
      state_,
      programLoaded_,
      fault_,
      /*pickupOk*/        true,
      /*materialPresent*/ false,
      /*laserSafe*/       false,
      /*estopHw*/         estopHw_,
  };
  // TODO: populate sensor fields from the HAL / SafetyMonitor.
}

}  // namespace pnp
