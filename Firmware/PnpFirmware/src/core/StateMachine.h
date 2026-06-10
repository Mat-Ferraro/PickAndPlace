#pragma once
#include <stdint.h>
#include "State.h"
#include "../hal/IMachine.h"
#include "ProgramStore.h"
#include "Interpreter.h"

// The 7-state machine + command gating, ported from simulator.py's StateMachine.
// Portable: no Arduino.h. Time is INJECTED via nowMs so transitions are
// deterministic under host tests (no sleeps / no wall-clock reads).

namespace pnp {

// A command after the protocol layer has parsed it off the wire.
struct Command {
  const char* name  = "";
  int32_t     id    = -1;

  // Payload fields used by transfer commands.
  uint32_t    size   = 0;     // begin_transfer
  uint16_t    chunks = 0;     // begin_transfer
  uint16_t    index  = 0;     // program_chunk
  const char* data   = "";    // program_chunk (base64)
};

// What the protocol layer should send back.
struct Response {
  enum Kind : uint8_t { Ack, Nack, None };
  Kind        kind;
  int32_t     id;
  const char* cmd;
  const char* reason;    // Nack reason / ack detail key

  // Extra fields for load_program ack (mirrors Python's response).
  int         instrCount = 0;
  uint32_t    bytes      = 0;

  Response() : kind(None), id(-1), cmd(""), reason("") {}
  Response(Kind k, int32_t i, const char* c, const char* r)
      : kind(k), id(i), cmd(c), reason(r) {}
};

// Snapshot for the periodic status broadcast.
struct StatusSnapshot {
  State       state;
  bool        programLoaded;
  const char* fault;
  bool        pickupOk;
  bool        materialPresent;
  bool        laserSafe;
  bool        estopHw;
};

class StateMachine {
 public:
  explicit StateMachine(IMachine& machine)
      : machine_(machine), interp_(machine, abortFlags_) {}

  Response       handleCommand(const Command& cmd, uint32_t nowMs);
  void           tick(uint32_t nowMs);
  void           pressButton(const char* button, uint32_t nowMs);
  void           setEstopHardware(bool active);
  void           injectFault(const char* reason);
  StatusSnapshot buildStatus() const;

  State       state()         const { return state_; }
  bool        programLoaded() const { return store_.programLoaded(); }
  const char* fault()         const { return fault_; }

  // Direct program load for testing (bypasses chunked transfer).
  void setProgramLoaded(bool v);

  static constexpr uint32_t kHomingMs = 3000;

 private:
  void     enterHoming(uint32_t nowMs);
  Response handleTransferCommand(const Command& cmd);
  Response ack(const Command& c) const;
  Response nack(const Command& c, const char* reason) const;

  IMachine&    machine_;
  AbortFlags   abortFlags_;
  Interpreter  interp_;
  ProgramStore store_;

  State       state_          = State::Idle;
  const char* fault_          = nullptr;
  bool        estopHw_        = false;
  uint32_t    homingDeadline_ = 0;

  // Scratch buffer for transfer error details.
  char        xferErr_[80]    = {};
};

}  // namespace pnp
