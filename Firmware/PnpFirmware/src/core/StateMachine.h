#pragma once
#include <stdint.h>
#include "State.h"
#include "../hal/IMachine.h"
#include "ProgramStore.h"
#include "Interpreter.h"
#include "../config/Config.h"

namespace pnp {

struct Command {
  const char* name  = "";
  int32_t     id    = -1;

  // begin_transfer / program_chunk / end_transfer
  uint32_t    size   = 0;
  uint16_t    chunks = 0;
  uint16_t    index  = 0;
  const char* data   = "";

  // calibrate_axis / set_cal_distance
  char        calAxis    = 'X';   // 'X', 'Y', or 'Z'
  float       calDistMm  = 0.0f;
};

struct Response {
  enum Kind : uint8_t { Ack, Nack, None };
  Kind        kind;
  int32_t     id;
  const char* cmd;
  const char* reason;
  int         instrCount = 0;
  uint32_t    bytes      = 0;

  Response() : kind(None), id(-1), cmd(""), reason("") {}
  Response(Kind k, int32_t i, const char* c, const char* r)
      : kind(k), id(i), cmd(c), reason(r) {}
};

struct StatusSnapshot {
  State       state;
  bool        programLoaded;
  const char* fault;
  bool        pickupOk;
  bool        materialPresent;
  bool        laserSafe;
  bool        estopHw;
  // Calibration fields
  char        calAxis;         // axis being calibrated (0 = none)
  uint32_t    calSteps;        // raw steps from traverse (0 = traverse not done)
};

class StateMachine {
 public:
  StateMachine(IMachine& machine, Config& config)
      : machine_(machine), config_(config), interp_(machine, abortFlags_) {}

  Response       handleCommand(const Command& cmd, uint32_t nowMs);
  void           tick(uint32_t nowMs);
  void           pressButton(const char* button, uint32_t nowMs);
  void           setEstopHardware(bool active);
  void           injectFault(const char* reason);
  StatusSnapshot buildStatus() const;

  State       state()         const { return state_; }
  bool        programLoaded() const { return store_.programLoaded(); }
  const char* fault()         const { return fault_; }

  // Calibration accessors (for tests and future Config wiring).
  float    stepsPerMm(char axis) const;
  uint32_t calSteps()            const { return calRawSteps_; }
  bool     calTraverseDone()     const { return calTraverseDone_; }

  void setProgramLoaded(bool v);

  static constexpr uint32_t kHomingMs = 3000;

 private:
  static int   axisIndex(char axis);   // 'X'→0, 'Y'→1, 'Z'→2, else -1
  void         enterHoming(uint32_t nowMs);
  Response     handleTransferCommand(const Command& cmd);
  Response     ack(const Command& c)  const;
  Response     nack(const Command& c, const char* reason) const;

  IMachine&    machine_;
  Config&      config_;
  AbortFlags   abortFlags_;
  Interpreter  interp_;
  ProgramStore store_;

  State       state_          = State::Idle;
  const char* fault_          = nullptr;
  bool        estopHw_        = false;
  uint32_t    homingDeadline_ = 0;

  // Calibration state
  char     calAxis_         = 'X';
  uint32_t calRawSteps_     = 0;
  bool     calTraverseDone_ = false;
  char     xferErr_[80] = {};
};

}  // namespace pnp
