#include "../platform/Platform.h"
#include "Interpreter.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <math.h>

namespace pnp {

// ============================================================
// Load
// ============================================================

void Interpreter::load(JsonObjectConst program) {
    prog_      = program;
    varCount_  = 0;
    callDepth_ = 0;
    stepIndex_ = 0;
    currentOp_ = nullptr;
    faultReason_[0] = '\0';

    // Cache config values (mirrors Python's self._cfg.get(..., default)).
    JsonObjectConst cfg = program["config"];
    if (!cfg.isNull()) {
        if (cfg["probe_step_mm"].is<float>())
            cfgProbeStep_ = cfg["probe_step_mm"].as<float>();
        if (cfg["probe_max_depth_mm"].is<float>())
            cfgProbeMaxDepth_ = cfg["probe_max_depth_mm"].as<float>();
        if (cfg["probe_threshold_mm"].is<float>())
            cfgProbeThresh_ = cfg["probe_threshold_mm"].as<float>();
        if (cfg["default_speed_pct"].is<int>())
            cfgDefaultSpeed_ = (uint8_t)cfg["default_speed_pct"].as<int>();
    }
}

// ============================================================
// run()
// ============================================================

OpResult Interpreter::run() {
    JsonArrayConst body = prog_["program"];
    if (body.isNull()) return fault("program_error: missing program array");

    OpResult r = execBody(body);
    // HALT and RETURN at top level are normal completion.
    if (r == OpResult::Faulted) {
        // Check if it's actually a "halt" signal (we encode it as a known
        // fault reason so we can distinguish from real faults).
        if (PNP_STREQ(faultReason_, "_halt")) {
            faultReason_[0] = '\0';
            return OpResult::Ok;
        }
    }
    return r;
}

// ============================================================
// execBody / execOne — the dispatch loop
// ============================================================

OpResult Interpreter::execBody(JsonArrayConst instrs) {
    for (JsonObjectConst instr : instrs) {
        OpResult r = check();
        if (r != OpResult::Ok) return r;
        r = execOne(instr);
        if (r != OpResult::Ok) return r;
    }
    return OpResult::Ok;
}

OpResult Interpreter::execOne(JsonObjectConst instr) {
    const char* op = instr["op"] | "";
    currentOp_ = op;
    stepIndex_++;

    if (strcmp(op, "MOVE")        == 0) return execMove(instr);
    if (strcmp(op, "PROBE_Z")     == 0) return execProbeZ(instr);
    if (strcmp(op, "HOME")        == 0) return execHome(instr);
    if (strcmp(op, "OUTPUT")      == 0) return execOutput(instr);
    if (PNP_STREQ(op, "READ_SENSOR")) return execReadSensor(instr);
    if (strcmp(op, "WAIT")        == 0) return execWait(instr);
    if (strcmp(op, "DELAY")       == 0) return execDelay(instr);
    if (strcmp(op, "LOOP_FOR")    == 0) return execLoopFor(instr);
    if (strcmp(op, "LOOP_WHILE")  == 0) return execLoopWhile(instr);
    if (strcmp(op, "IF")          == 0) return execIf(instr);
    if (strcmp(op, "CALL")        == 0) return execCall(instr);
    if (strcmp(op, "SET_VAR")     == 0) return execSetVar(instr);
    if (strcmp(op, "LOG")         == 0) return execLog(instr);
    if (strcmp(op, "LABEL")       == 0) return OpResult::Ok;  // noop
    if (strcmp(op, "RETURN")      == 0) return fault("_return");  // signal
    if (strcmp(op, "HALT")        == 0) return fault("_halt");    // signal
    if (strcmp(op, "JUMP")        == 0)
        return fault("program_error: JUMP is not supported in interpreter v1; "
                     "use LOOP_WHILE or CALL/RETURN for flow control");

    if (PNP_STREQ(op, "FAULT")) {
        const char* reason = instr["reason"] | "program_fault";
        char expanded[64];
        expand(reason, expanded, sizeof(expanded));
        return fault(expanded);
    }

    char msg[64];
    PNP_SNPRINTF(msg, sizeof(msg), "program_error: unknown op '%s'", op);
    return fault(msg);
}

// ============================================================
// Motion
// ============================================================

OpResult Interpreter::execMove(JsonObjectConst i) {
    uint8_t speed = cfgDefaultSpeed_;
    if (i["speed"].is<int>()) speed = (uint8_t)i["speed"].as<int>();

    // via waypoints
    JsonArrayConst via = i["via"];
    if (!via.isNull()) {
        for (JsonObjectConst wp : via) {
            float wx, wy, wz;
            if (!resolveFloat(wp["x"], wx) ||
                !resolveFloat(wp["y"], wy) ||
                !resolveFloat(wp["z"], wz))
                return OpResult::Faulted;
            OpResult r = machine_.moveTo(wx, wy, wz, speed);
            if (r != OpResult::Ok) return r;
            r = check();
            if (r != OpResult::Ok) return r;
        }
    }

    float x, y, z;
    if (!resolveFloat(i["x"], x) ||
        !resolveFloat(i["y"], y) ||
        !resolveFloat(i["z"], z))
        return OpResult::Faulted;
    return machine_.moveTo(x, y, z, speed);
}

OpResult Interpreter::execProbeZ(JsonObjectConst i) {
    float x, y;
    if (!resolveFloat(i["x"], x) || !resolveFloat(i["y"], y))
        return OpResult::Faulted;

    // approach_z optional — defaults to current Z (mirrors Python)
    float approachZ;
    if (i["approach_z"].isNull()) {
        Position pos = machine_.getPosition();
        approachZ = pos.z;
    } else {
        if (!resolveFloat(i["approach_z"], approachZ)) return OpResult::Faulted;
    }

    float resultZ;
    OpResult r = machine_.probeZ(x, y, approachZ,
                                  cfgProbeStep_, cfgProbeMaxDepth_,
                                  cfgProbeThresh_, resultZ);
    if (r != OpResult::Ok) return r;

    const char* store = i["store"] | "";
    if (!setVar(store, resultZ)) return fault("program_error: too many variables");
    return OpResult::Ok;
}

OpResult Interpreter::execHome(JsonObjectConst i) {
    // Collect axes string e.g. "XYZ" from the axes array ["X","Y","Z"]
    char axes[8] = {};
    int idx = 0;
    for (JsonVariantConst a : i["axes"].as<JsonArrayConst>()) {
        const char* s = a | "";
        if (idx < 7) axes[idx++] = s[0];
    }
    axes[idx] = '\0';
    return machine_.home(axes);
}

// ============================================================
// I/O
// ============================================================

OpResult Interpreter::execOutput(JsonObjectConst i) {
    const char* name = i["name"] | "";
    // value can be bool or string; pass as bool (string outputs like
    // servo positions go through setOutput with value=true as a placeholder
    // until the real HAL interprets the name)
    bool val = i["value"].as<bool>();
    machine_.setOutput(name, val);
    return OpResult::Ok;
}

OpResult Interpreter::execReadSensor(JsonObjectConst i) {
    const char* sensor = i["sensor"] | "";
    const char* store  = i["store"]  | "";
    float val = machine_.readSensor(sensor) ? 1.0f : 0.0f;
    if (!setVar(store, val)) return fault("program_error: too many variables");
    return OpResult::Ok;
}

// ============================================================
// Wait / timing
// ============================================================

OpResult Interpreter::execWait(JsonObjectConst i) {
    const char* condition   = i["condition"] | "";
    bool        hasTimeout  = !i["timeout_ms"].isNull();
    uint32_t    timeoutMs   = hasTimeout ? (uint32_t)i["timeout_ms"].as<int>() : 0;
    const char* timeoutFault = i["timeout_fault"] | "wait_timeout";
    uint32_t    start       = millis();

    while (true) {
        bool satisfied;
        if (!evalCondition(condition, satisfied)) return OpResult::Faulted;
        if (satisfied) return OpResult::Ok;

        OpResult r = check();
        if (r != OpResult::Ok) return r;

        if (hasTimeout && (uint32_t)(millis() - start) >= timeoutMs) {
            char expanded[64];
            expand(timeoutFault, expanded, sizeof(expanded));
            return fault(expanded);
        }
        // Small yield — on AVR this is a busy loop; on host it burns CPU but
        // tests are short-lived. A real RTOS port would call taskYield().
    }
}

OpResult Interpreter::execDelay(JsonObjectConst i) {
    float ms;
    if (!resolveFloat(i["ms"], ms)) return OpResult::Faulted;
    return machine_.delayMs((uint32_t)ms);
}

// ============================================================
// Flow control
// ============================================================

OpResult Interpreter::execLoopFor(JsonObjectConst i) {
    float countF;
    if (!resolveFloat(i["count"], countF)) return OpResult::Faulted;
    int32_t count = (int32_t)countF;
    JsonArrayConst body = i["body"];

    for (int32_t idx = 0; idx < count; idx++) {
        setVar("_loop_i", (float)idx);
        OpResult r = execBody(body);
        if (r != OpResult::Ok) return r;
    }
    return OpResult::Ok;
}

OpResult Interpreter::execLoopWhile(JsonObjectConst i) {
    const char*    condition = i["condition"] | "";
    JsonArrayConst body      = i["body"];
    int32_t        iterations = 0;

    while (true) {
        bool satisfied;
        if (!evalCondition(condition, satisfied)) return OpResult::Faulted;
        if (!satisfied) break;

        if (iterations >= kMaxLoopIter) return fault("loop_overflow");
        iterations++;

        OpResult r = execBody(body);
        if (r != OpResult::Ok) return r;
    }
    return OpResult::Ok;
}

OpResult Interpreter::execIf(JsonObjectConst i) {
    bool satisfied;
    if (!evalCondition(i["condition"] | "", satisfied)) return OpResult::Faulted;
    if (satisfied) return execBody(i["then"]);
    JsonArrayConst elseBody = i["else"];
    if (!elseBody.isNull()) return execBody(elseBody);
    return OpResult::Ok;
}

OpResult Interpreter::execCall(JsonObjectConst i) {
    const char* subName = i["sub"] | "";
    JsonArrayConst body = prog_["subroutines"][subName];
    if (body.isNull()) {
        char msg[64];
        snprintf(msg, sizeof(msg),
                 "program_error: unknown subroutine '%s'", subName);
        return fault(msg);
    }
    callDepth_++;
    if (callDepth_ > kMaxCallDepth) {
        callDepth_--;
        return fault("call_depth");
    }
    OpResult r = execBody(body);
    callDepth_--;
    // A RETURN from inside the subroutine is encoded as Faulted+"_return" —
    // intercept it here and treat as normal return.
    if (r == OpResult::Faulted && PNP_STREQ(faultReason_, "_return")) {
        faultReason_[0] = '\0';
        return OpResult::Ok;
    }
    return r;
}

// ============================================================
// Variables / log
// ============================================================

OpResult Interpreter::execSetVar(JsonObjectConst i) {
    const char* name = i["name"] | "";
    float val;
    if (!i["expr"].isNull()) {
        // expression: substitute $vars then evaluate arithmetic
        const char* expr = i["expr"] | "";
        char expanded[64];
        expand(expr, expanded, sizeof(expanded));
        if (!evalExpr(expanded, val)) return OpResult::Faulted;
    } else {
        JsonVariantConst v = i["value"];
        if (v.is<bool>())  val = v.as<bool>() ? 1.0f : 0.0f;
        else if (v.is<float>() || v.is<int>()) val = v.as<float>();
        else val = 0.0f;
    }
    if (!setVar(name, val)) return fault("program_error: too many variables");
    return OpResult::Ok;
}

OpResult Interpreter::execLog(JsonObjectConst i) {
    const char* msg = i["message"] | "";
    char expanded[128];
    expand(msg, expanded, sizeof(expanded));
    machine_.log(expanded);
    return OpResult::Ok;
}

// ============================================================
// Cooperative cancellation
// ============================================================

OpResult Interpreter::check() {
    // Spin while paused (mirrors Python's pause loop).
    while (flags_.pause && !flags_.stop) { /* yield */ }
    if (flags_.stop) {
        return fault("estop_triggered");
    }
    return OpResult::Ok;
}

// ============================================================
// Variable store
// ============================================================

bool Interpreter::setVar(const char* name, float value) {
    // Update existing slot.
    for (int i = 0; i < varCount_; i++) {
        if (strncmp(vars_[i].name, name, kMaxVarName) == 0) {
            vars_[i].value = value;
            return true;
        }
    }
    // New slot.
    if (varCount_ >= kMaxVars) return false;
    strncpy(vars_[varCount_].name, name, kMaxVarName - 1);
    vars_[varCount_].name[kMaxVarName - 1] = '\0';
    vars_[varCount_].value = value;
    varCount_++;
    return true;
}

float Interpreter::getVarFloat(const char* name, bool& ok) const {
    for (int i = 0; i < varCount_; i++) {
        if (strncmp(vars_[i].name, name, kMaxVarName) == 0) {
            ok = true;
            return vars_[i].value;
        }
    }
    ok = false;
    return 0.0f;
}

bool Interpreter::getVar(const char* name, float& outVal) const {
    bool ok;
    outVal = getVarFloat(name, ok);
    return ok;
}

bool Interpreter::resolveFloat(JsonVariantConst v, float& out) {
    if (v.is<float>() || v.is<int>()) {
        out = v.as<float>();
        return true;
    }
    if (v.is<const char*>()) {
        const char* s = v.as<const char*>();
        if (s && s[0] == '$') {
            bool ok;
            out = getVarFloat(s + 1, ok);
            if (!ok) {
                char msg[64];
                snprintf(msg, sizeof(msg),
                         "program_error: undefined variable %s", s);
                fault(msg);
                return false;
            }
            return true;
        }
    }
    out = 0.0f;
    return true;
}

// ============================================================
// String expansion  ($var -> value)
// ============================================================

void Interpreter::expand(const char* tmpl, char* outBuf, size_t outLen) {
    size_t wi = 0;
    const char* p = tmpl;
    while (*p && wi < outLen - 1) {
        if (*p == '$') {
            p++;
            char varName[kMaxVarName];
            int ni = 0;
            while (*p && ((*p >= 'a' && *p <= 'z') ||
                          (*p >= 'A' && *p <= 'Z') ||
                          (*p >= '0' && *p <= '9') ||
                          *p == '_') && ni < kMaxVarName - 1) {
                varName[ni++] = *p++;
            }
            varName[ni] = '\0';
            bool ok;
            float val = getVarFloat(varName, ok);
            char numBuf[32];
            if (ok) {
                // Whole floats render as ints (mirrors Python behaviour)
                if (val == (float)(int)val)
                    PNP_SNPRINTF(numBuf, sizeof(numBuf), "%d", (int)val);
                else
                    PNP_SNPRINTF(numBuf, sizeof(numBuf), "%.6g", val);
            } else {
                PNP_SNPRINTF(numBuf, sizeof(numBuf), "$%s", varName);
            }
            const char* q = numBuf;
            while (*q && wi < outLen - 1) outBuf[wi++] = *q++;
        } else {
            outBuf[wi++] = *p++;
        }
    }
    outBuf[wi] = '\0';
}

// ============================================================
// Condition evaluator
// ============================================================

// Named sensor check — PNP_STREQ keeps all literals in flash on AVR.
static bool isSensor(const char* s) {
    return PNP_STREQ(s, "material_present") ||
           PNP_STREQ(s, "pickup_ok")        ||
           PNP_STREQ(s, "laser_safe")       ||
           PNP_STREQ(s, "estop_hw");
}

bool Interpreter::evalCondition(const char* rawCond, bool& result) {
    // Trim leading spaces
    while (*rawCond == ' ') rawCond++;

    if (strcmp(rawCond, "true")  == 0) { result = true;  return true; }
    if (PNP_STREQ(rawCond, "false")) { result = false; return true; }

    // "not <cond>"
    if (strncmp(rawCond, "not ", 4) == 0) {
        bool inner;
        if (!evalCondition(rawCond + 4, inner)) return false;
        result = !inner;
        return true;
    }

    // Named sensor condition
    if (isSensor(rawCond)) {
        result = machine_.readSensor(rawCond);
        return true;
    }

    // Variable comparison: $var op literal
    // Pattern: starts with '$', then varname, then whitespace+op+whitespace+rhs
    if (rawCond[0] == '$') {
        const char* p = rawCond + 1;
        char varName[kMaxVarName];
        int ni = 0;
        while (*p && *p != ' ' && ni < kMaxVarName - 1)
            varName[ni++] = *p++;
        varName[ni] = '\0';
        while (*p == ' ') p++;

        // Identify operator
        const char* ops[] = {"==", "!=", "<=", ">=", "<", ">", nullptr};
        int opIdx = -1;
        int opLen = 0;
        for (int i = 0; ops[i]; i++) {
            if (strncmp(p, ops[i], strlen(ops[i])) == 0) {
                opIdx = i; opLen = (int)strlen(ops[i]); break;
            }
        }
        if (opIdx < 0) {
            char msg[80];
            PNP_SNPRINTF(msg, sizeof(msg), "program_error: invalid condition '%s'", rawCond);
            fault(msg);
            return false;
        }
        p += opLen;
        while (*p == ' ') p++;

        // LHS variable
        bool ok;
        float lhs = getVarFloat(varName, ok);
        if (!ok) {
            char msg[64];
            PNP_SNPRINTF(msg, sizeof(msg), "program_error: undefined variable $%s", varName);
            fault(msg);
            return false;
        }

        // RHS literal
        float rhs;
        if (strcmp(p, "true")  == 0) rhs = 1.0f;
        else if (PNP_STREQ(p, "false")) rhs = 0.0f;
        else {
            char* end;
            rhs = (float)strtod(p, &end);
            if (end == p) {
                char msg[80];
                snprintf(msg, sizeof(msg),
                         "program_error: cannot parse condition rhs '%s'", p);
                fault(msg);
                return false;
            }
        }

        switch (opIdx) {
            case 0: result = (lhs == rhs); break;
            case 1: result = (lhs != rhs); break;
            case 2: result = (lhs <= rhs); break;
            case 3: result = (lhs >= rhs); break;
            case 4: result = (lhs <  rhs); break;
            case 5: result = (lhs >  rhs); break;
        }
        return true;
    }

    char msg[80];
    PNP_SNPRINTF(msg, sizeof(msg), "program_error: invalid condition '%s'", rawCond);
    fault(msg);
    return false;
}

// ============================================================
// Simple arithmetic expression evaluator
// Handles: +, -, *, /, parentheses, float literals.
// Called AFTER $var substitution so only numeric tokens remain.
// ============================================================

namespace {
    struct Expr {
        const char* p;
        float result;
        bool  ok;
        char  errBuf[64];

        float parseExpr();       // additive
        float parseTerm();       // multiplicative
        float parseFactor();     // unary minus, parentheses, literal
        void  skipSpace() { while (*p == ' ') p++; }
    };

    float Expr::parseExpr() {
        float v = parseTerm();
        while (ok) {
            skipSpace();
            if (*p == '+') { p++; v += parseTerm(); }
            else if (*p == '-') { p++; v -= parseTerm(); }
            else break;
        }
        return v;
    }
    float Expr::parseTerm() {
        float v = parseFactor();
        while (ok) {
            skipSpace();
            if (*p == '*') { p++; v *= parseFactor(); }
            else if (*p == '/') {
                p++;
                float d = parseFactor();
                if (d == 0.0f) { ok = false; PNP_SNPRINTF(errBuf, sizeof(errBuf), "division by zero"); return 0; }
                v /= d;
            }
            else break;
        }
        return v;
    }
    float Expr::parseFactor() {
        skipSpace();
        if (*p == '(') {
            p++;
            float v = parseExpr();
            skipSpace();
            if (*p == ')') p++; else { ok = false; PNP_SNPRINTF(errBuf, sizeof(errBuf), "mismatched parenthesis"); }
            return v;
        }
        if (*p == '-') { p++; return -parseFactor(); }
        char* end;
        float v = (float)strtod(p, &end);
        if (end == p) {
            ok = false;
            PNP_SNPRINTF(errBuf, sizeof(errBuf), "unexpected token '%c'", *p ? *p : '?');
            return 0;
        }
        p = end;
        return v;
    }
}

bool Interpreter::evalExpr(const char* expr, float& result) {
    // Safety check: only allow safe arithmetic characters (mirrors Python).
    for (const char* c = expr; *c; c++) {
        if (!((*c >= '0' && *c <= '9') || *c == '.' || *c == ' ' ||
              *c == '+' || *c == '-' || *c == '*' || *c == '/' ||
              *c == '(' || *c == ')')) {
            char msg[64];
            PNP_SNPRINTF(msg, sizeof(msg), "program_error: unsafe expression '%s'", expr);
            fault(msg);
            return false;
        }
    }
    Expr e;
    e.p  = expr;
    e.ok = true;
    e.errBuf[0] = '\0';
    result = e.parseExpr();
    if (!e.ok) {
        char msg[128];
        PNP_SNPRINTF(msg, sizeof(msg), "program_error: invalid expression '%s': %s",
                 expr, e.errBuf);
        fault(msg);
        return false;
    }
    return true;
}

// ============================================================
// Fault helper
// ============================================================

OpResult Interpreter::fault(const char* reason) {
    strncpy(faultReason_, reason, sizeof(faultReason_) - 1);
    faultReason_[sizeof(faultReason_) - 1] = '\0';
    return OpResult::Faulted;
}

}  // namespace pnp
