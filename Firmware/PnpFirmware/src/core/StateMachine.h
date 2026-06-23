#pragma once
#include <stdint.h>
#include "State.h"
#include "../hal/IMachine.h"
#include "ProgramStore.h"
#include "Interpreter.h"
#include "../config/Config.h"

namespace pnp {

// Axis identifiers for calibration commands.
// Y1 = Y socket (D60/61/56), Y2 = E0 socket (D26/28/24).
enum class CalAxis : uint8_t { X=0, Y=1, Z=2, Invalid=0xFF };

// Parse an axis string to CalAxis. "Y", "Y1", and "Y2" all map to the single
// Y axis — the two gantry motors share one steps/mm and calibrate together.
inline CalAxis parseCalAxis(const char* s) {
    if (!s || !s[0]) return CalAxis::Invalid;
    if (s[0]=='X' && !s[1])  return CalAxis::X;
    if (s[0]=='Z' && !s[1])  return CalAxis::Z;
    if (s[0]=='Y' && (!s[1] || ((s[1]=='1'||s[1]=='2') && !s[2]))) return CalAxis::Y;
    return CalAxis::Invalid;
}
inline const char* calAxisName(CalAxis a) {
    switch (a) {
        case CalAxis::X:  return "X";
        case CalAxis::Y:  return "Y";   // HAL jogs BOTH Y motors in lockstep
        case CalAxis::Z:  return "Z";
        default:          return "?";
    }
}

struct Command {
  const char* name  = "";
  int32_t     id    = -1;

  // begin_transfer / program_chunk / end_transfer
  uint32_t    size   = 0;
  uint16_t    chunks = 0;
  uint16_t    index  = 0;
  const char* data   = "";

  // get_param
  const char* paramKey = "";

  // calibrate_axis / set_cal_distance / set_max_travel / cal_jog
  CalAxis     calAxis    = CalAxis::X;
  float       mm         = 0.0f;   // magnitude arg (cal distance or travel limit)
  int32_t     steps      = 0;      // cal_jog raw step count (signed = direction)

  // set_output / set_servo
  const char* output   = "";       // set_output: output name ("pump", ...)
  bool        state    = false;    // set_output: on/off
  const char* servo    = "";       // set_servo: servo name ("door", "laser_btn")
  const char* position = "";       // set_servo: "open"/"closed"/"press"/"release"
};

struct Response {
  enum Kind : uint8_t { Ack, Nack, None };
  Kind        kind;
  int32_t     id;
  const char* cmd;
  const char* reason;
  int         instrCount = 0;
  uint32_t    bytes      = 0;

  // get_param response payload
  bool        hasParamValue = false;
  float       paramValue    = 0.0f;
  const char* paramKey      = "";   // echoed back as "key" (the actual param, not the cmd)
  // calibrate_sensors response payload
  bool        hasTofOffsets = false;
  float       tofOffsets[4] = {0,0,0,0};
  // query_sensors response payload (live ToF distances, 6 mux channels)
  bool        hasTofReadings = false;
  float       tofDistMm[6]   = {0,0,0,0,0,0};
  bool        tofValid[6]    = {false,false,false,false,false,false};

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
  const char* calAxis;         // axis name being calibrated (nullptr = none)
  uint32_t    calSteps;        // net steps jogged so far (0 = none yet)
  // Output state (reflected to the GUI Service tab)
  bool        pump;            // pump on/off
  bool        valve;           // valve (solenoid) on/off
  const char* servoDoor;       // "open" / "closed"
  const char* servoLaserBtn;   // "press" / "release"
  // Physical input state (Service-tab Inputs panel)
  bool        startBtn;        // Start button currently pressed
  bool        pauseBtn;        // Pause button currently pressed
};

class StateMachine {
 public:
  StateMachine(IMachine& machine, Config& config)
      : machine_(machine), config_(config), interp_(machine, abortFlags_) {}

  Response       handleCommand(const Command& cmd, uint32_t nowMs);
  void           tick(uint32_t nowMs);
  void           pressButton(const char* button, uint32_t nowMs);
  void           setEstopHardware(bool active);
  void           setButtonLevels(bool start, bool pause) { startBtn_ = start; pauseBtn_ = pause; }
  void           injectFault(const char* reason);
  StatusSnapshot buildStatus() const;

  State       state()         const { return state_; }
  bool        programLoaded() const { return store_.programLoaded(); }
  const char* fault()         const { return fault_; }

  // Calibration accessors (for tests and future Config wiring).
  float    stepsPerMm(CalAxis axis) const;
  // Net steps jogged so far this calibration (magnitude). 0 = nothing jogged.
  uint32_t calSteps() const { return (uint32_t)(calJogSteps_ < 0 ? -calJogSteps_ : calJogSteps_); }

  void setProgramLoaded(bool v);

  static constexpr uint32_t kHomingMs = 3000;

 private:
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

  // Calibration state (jog-and-measure)
  CalAxis  calAxis_      = CalAxis::Invalid;
  int32_t  calJogSteps_  = 0;     // net steps jogged this session (signed)
  char     xferErr_[80] = {};

  // Output state (mirrored to the GUI Service tab via status)
  bool        pump_          = false;
  bool        valve_         = false;
  const char* servoDoor_     = "closed";
  const char* servoLaserBtn_ = "release";
  bool        startBtn_      = false;   // live (debounced) button levels
  bool        pauseBtn_      = false;
};

}  // namespace pnp