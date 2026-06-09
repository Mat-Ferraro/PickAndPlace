#pragma once
#include <stdint.h>
#include "State.h"
#include "../hal/IMachine.h"

// The 7-state machine + command gating, ported from simulator.py's StateMachine.
// Portable: no Arduino.h. Time is INJECTED via nowMs so transitions are
// deterministic under host tests (no sleeps / no wall-clock reads).

namespace pnp {

// A command after the protocol layer has parsed it off the wire. The scaffold
// only needs name + id for gating and the core transitions; payload fields
// (coords, names, values) get added as each command is implemented.
struct Command {
  const char* name = "";
  int32_t     id   = -1;   // -1 = no id supplied
};

// What the protocol layer should send back. The state machine never touches
// JSON or Serial itself — it returns a Response and the protocol serialises it.
struct Response {
  enum Kind : uint8_t { Ack, Nack, None } kind = None;
  int32_t     id     = -1;
  const char* cmd    = "";
  const char* reason = "";   // populated for Nack
};

// A snapshot for the periodic status broadcast (protocol serialises this).
struct StatusSnapshot {
  State       state;
  bool        programLoaded;
  const char* fault;            // nullptr when no fault
  bool        pickupOk;
  bool        materialPresent;
  bool        laserSafe;
  bool        estopHw;
};

class StateMachine {
 public:
  explicit StateMachine(IMachine& machine) : machine_(machine) {}

  // Serial command path: gate against the current state, apply the transition,
  // and return Ack/Nack for the protocol layer to send.
  Response handleCommand(const Command& cmd, uint32_t nowMs);

  // Time-driven transitions (e.g. homing completion). Call every loop.
  void tick(uint32_t nowMs);

  // Physical button path: same internal transitions as the serial commands,
  // but produces NO Response — an attached GUI learns of the change from the
  // next status broadcast, exactly like real hardware.
  void pressButton(const char* button, uint32_t nowMs);

  // Hardware E-stop input edge (latched button / fail-safe NC line).
  void setEstopHardware(bool active);

  // Raised by the safety monitors (pickup loss, StallGuard jam, ...).
  void injectFault(const char* reason);

  StatusSnapshot buildStatus() const;

  State       state()         const { return state_; }
  bool        programLoaded() const { return programLoaded_; }
  const char* fault()         const { return fault_; }

  // Until Config/EEPROM is wired, programLoaded is set explicitly.
  void setProgramLoaded(bool v) { programLoaded_ = v; }

  static constexpr uint32_t kHomingMs = 3000;   // placeholder homing duration

 private:
  void     enterHoming(uint32_t nowMs);
  Response ack(const Command& c)  const;
  Response nack(const Command& c, const char* reason) const;

  IMachine&   machine_;
  State       state_          = State::Idle;
  bool        programLoaded_  = false;
  const char* fault_          = nullptr;
  bool        estopHw_        = false;
  uint32_t    homingDeadline_ = 0;
};

}  // namespace pnp
