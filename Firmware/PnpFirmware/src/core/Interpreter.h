#pragma once
#include <stdint.h>
#include <ArduinoJson.h>
#include "../hal/IMachine.h"
#include "../platform/Platform.h"
#include "../config/TravelLimits.h"

// C++ port of Software/interpreter.py — executes a validated job program
// against IMachine. Faithful to the Python: same ops, same rules, same fault
// reasons. The 71 interpreter tests in test_interpreter.py are translated
// directly into host Unity tests in test/test_interpreter.cpp.
//
// Adaptations from Python for AVR:
//   - No exceptions: run() returns OpResult; fault reason via faultReason().
//   - No threading.Event: AbortFlags struct (two volatile bools).
//   - No std::map: fixed VarStore (kMaxVars slots, float values).
//   - No regex / eval: manual condition parser and arithmetic evaluator.
//   - No time.sleep in WAIT: polls readSensor() + millis() timeout.

namespace pnp {

// Two bools the StateMachine sets to cooperatively cancel the interpreter.
// volatile because they may be written from an ISR (E-stop hardware line).
struct AbortFlags {
    volatile bool stop  = false;   // E-stop: abort immediately
    volatile bool pause = false;   // pause: hold between instructions
};

class Interpreter {
 public:
    static constexpr int     kMaxVars      = 8;
    static constexpr int     kMaxVarName   = 16;
    static constexpr int     kMaxCallDepth = 8;
    static constexpr int32_t kMaxLoopIter  = 10000;

    // Variable entry — all values stored as float (bool as 0.0/1.0).
    struct Var {
        char  name[kMaxVarName];
        float value;
    };

    // Snapshot for status broadcasts.
    struct Status {
        const char* currentOp;   // nullptr between instructions
        int32_t     stepIndex;
    };

    explicit Interpreter(IMachine& machine, AbortFlags& flags)
        : machine_(machine), flags_(flags) {}

    // Load a validated program. Keeps a reference to the document — caller
    // must ensure it outlives the Interpreter.
    void load(JsonObjectConst program);

    // Push the active soft travel envelope before run(). The StateMachine calls
    // this from run_program with the current Config limits, so a MOVE outside
    // [0, maxTravelMm] faults. Independent of load() — the envelope is machine
    // configuration, not part of the program. Defaults to disabled (unbounded).
    void setTravelLimits(const TravelLimits& limits) { limits_ = limits; }

    // Run to completion (blocking). Returns:
    //   Ok      — normal completion or HALT
    //   Aborted — E-stop fired (stop flag set)
    //   Faulted — program error; call faultReason() for the reason string
    OpResult run();

    const char* faultReason() const { return faultReason_; }
    Status      status()      const { return {currentOp_, stepIndex_}; }

    // Variable access for tests.
    bool  getVar(const char* name, float& outVal) const;
    int   varCount() const { return varCount_; }

 private:
    // ---- execution ----
    OpResult execBody(JsonArrayConst instrs);
    OpResult execOne(JsonObjectConst instr);

    // Single chokepoint for all programmatic motion: enforces the soft travel
    // envelope (limits_) then delegates to machine_.moveTo. Both the MOVE
    // waypoints and the final target route through here.
    OpResult guardedMove(float x, float y, float z, uint8_t speed);

    OpResult execMove(JsonObjectConst i);
    OpResult execProbeZ(JsonObjectConst i);    OpResult execHome(JsonObjectConst i);
    OpResult execOutput(JsonObjectConst i);
    OpResult execReadSensor(JsonObjectConst i);
    OpResult execWait(JsonObjectConst i);
    OpResult execDelay(JsonObjectConst i);
    OpResult execLoopFor(JsonObjectConst i);
    OpResult execLoopWhile(JsonObjectConst i);
    OpResult execIf(JsonObjectConst i);
    OpResult execCall(JsonObjectConst i);
    OpResult execSetVar(JsonObjectConst i);
    OpResult execLog(JsonObjectConst i);

    // ---- cooperative cancellation ----
    OpResult check();   // pause/stop point — call between instructions

    // ---- variable store ----
    bool  setVar(const char* name, float value);
    bool  resolveFloat(JsonVariantConst v, float& out);   // literal or $name
    float getVarFloat(const char* name, bool& ok) const;

    // ---- condition / expression evaluation ----
    // Returns true/false or stores error in faultReason_ and returns false.
    bool evalCondition(const char* cond, bool& result);
    bool evalExpr(const char* expr, float& result);

    // ---- string helpers ----
    // Expand $vars in a string into outBuf (truncated to outLen).
    void expand(const char* tmpl, char* outBuf, size_t outLen);

    // ---- fault helpers ----
    OpResult fault(const char* reason);  // sets faultReason_, returns Faulted

    // ---- state ----
    IMachine&      machine_;
    AbortFlags&    flags_;
    TravelLimits   limits_;     // soft work envelope; disabled => unbounded
    JsonObjectConst prog_;      // root program object (loaded)
    float           cfgProbeStep_     = 0.5f;
    float           cfgProbeMaxDepth_ = 200.0f;
    float           cfgProbeThresh_   = 40.0f;
    uint8_t         cfgDefaultSpeed_  = 80;

    Var        vars_[kMaxVars];
    int        varCount_    = 0;
    int        callDepth_   = 0;
    int32_t    stepIndex_   = 0;
    const char* currentOp_ = nullptr;
    char        faultReason_[64] = {};
};

}  // namespace pnp