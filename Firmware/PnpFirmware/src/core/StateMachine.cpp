#include "StateMachine.h"
#include "../platform/Platform.h"
#include <string.h>
#include <stdio.h>

namespace pnp {

// ============================================================
// Gating
// ============================================================

static uint8_t allowedStates(const char* name) {
    if (PNP_STREQ(name, "home"))            return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "load_program"))    return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "run_program"))     return stbit(State::Ready);
    if (PNP_STREQ(name, "pause"))           return stbit(State::Running);
    if (PNP_STREQ(name, "resume"))          return stbit(State::Paused);
    if (PNP_STREQ(name, "reset_fault"))     return stbit(State::Faulted);
    if (PNP_STREQ(name, "reset_estop"))     return stbit(State::Estopped);
    if (PNP_STREQ(name, "jog"))             return stbit(State::Ready);
    if (PNP_STREQ(name, "teach_position"))  return stbit(State::Ready);
    if (PNP_STREQ(name, "move_to"))         return stbit(State::Ready);
    if (PNP_STREQ(name, "query_position"))  return uint8_t(stbit(State::Idle)|stbit(State::Ready)|
                                                            stbit(State::Faulted)|stbit(State::Estopped));
    if (PNP_STREQ(name, "save_position"))   return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "set_param"))       return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "save_config"))     return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "load_config"))     return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "begin_transfer"))  return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "program_chunk"))   return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "end_transfer"))    return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "set_output"))      return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "set_servo"))       return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "query_sensors"))   return uint8_t(stbit(State::Idle)|stbit(State::Ready)|
                                                            stbit(State::Running)|stbit(State::Paused)|
                                                            stbit(State::Faulted)|stbit(State::Estopped));
    // Calibration commands
    if (PNP_STREQ(name, "calibrate_axis"))     return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "calibrate_sensors"))  return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "set_cal_distance"))return stbit(State::Calibrating);
    if (PNP_STREQ(name, "cal_jog"))         return stbit(State::Calibrating);
    if (PNP_STREQ(name, "cancel_calibration")) return stbit(State::Calibrating);
    if (PNP_STREQ(name, "set_max_travel"))  return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "set_tof_threshold")) return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    return 0;
}

static bool isAlwaysAccept(const char* name) {
    return PNP_STREQ(name, "estop")           ||
           PNP_STREQ(name, "get_param")       ||
           PNP_STREQ(name, "query_status")    ||
           PNP_STREQ(name, "laser_safe")      ||
           PNP_STREQ(name, "query_positions") ||
           PNP_STREQ(name, "get_program");
}

// ============================================================
// Axis helpers
// ============================================================

float StateMachine::stepsPerMm(CalAxis axis) const {
    switch (axis) {
        case CalAxis::X:  return config_.stepsPerMmX;
        case CalAxis::Y:  return config_.stepsPerMmY;
        case CalAxis::Z:  return config_.stepsPerMmZ;
        default:          return 0.0f;
    }
}

// ============================================================
// Helpers
// ============================================================

Response StateMachine::ack(const Command& c) const {
    return Response{Response::Ack, c.id, c.name, ""};
}
Response StateMachine::nack(const Command& c, const char* reason) const {
    return Response{Response::Nack, c.id, c.name, reason};
}

void StateMachine::enterHoming(uint32_t nowMs) {
    machine_.home("XYZ");
    state_ = State::Homing;
    homingDeadline_ = nowMs + kHomingMs;
}

void StateMachine::setProgramLoaded(bool v) {
    if (!v) store_.reset();
}

// ============================================================
// handleTransferCommand
// ============================================================

Response StateMachine::handleTransferCommand(const Command& cmd) {
    const char* name = cmd.name;
    if (PNP_STREQ(name, "begin_transfer")) {
        ProgramStore::Result r = store_.beginTransfer(cmd.size, cmd.chunks);
        if (r == ProgramStore::Result::InvalidParam) return nack(cmd, "invalid_param");
        if (r == ProgramStore::Result::BufferFull)   return nack(cmd, "buffer_full");
        return ack(cmd);
    }
    if (PNP_STREQ(name, "program_chunk")) {
        ProgramStore::Result r = store_.receiveChunk(cmd.index, cmd.data);
        switch (r) {
            case ProgramStore::Result::NoTransferInProgress: return nack(cmd, "no_transfer_in_progress");
            case ProgramStore::Result::BadBase64:            return nack(cmd, "bad_base64");
            case ProgramStore::Result::BufferFull:           return nack(cmd, "buffer_full");
            case ProgramStore::Result::OutOfOrder:
                PNP_SNPRINTF(xferErr_, sizeof(xferErr_),
                             "out_of_order_expected_%u", (unsigned)store_.xferReceived());
                return nack(cmd, xferErr_);
            default: break;
        }
        Response r2 = ack(cmd);
        r2.instrCount = (int)cmd.index;
        return r2;
    }
    if (PNP_STREQ(name, "end_transfer")) {
        ProgramStore::Result r = store_.endTransfer(xferErr_, sizeof(xferErr_));
        if (r != ProgramStore::Result::Ok) return nack(cmd, xferErr_);
        Response resp(Response::Ack, cmd.id, "load_program", "");
        resp.instrCount = store_.instructionCount();
        resp.bytes      = (uint32_t)store_.programBytes();
        return resp;
    }
    return nack(cmd, "unknown_command");
}

// ============================================================
// handleCommand
// ============================================================

Response StateMachine::handleCommand(const Command& cmd, uint32_t nowMs) {
    const char* name = cmd.name;

    if (PNP_STREQ(name, "estop")) { setEstopHardware(true); return ack(cmd); }
    if (isAlwaysAccept(name)) {
        if (PNP_STREQ(name, "get_param")) {
            Response r = ack(cmd);
            const char* k = cmd.paramKey;
            r.paramKey = k;
            if      (PNP_STREQ(k, "steps_per_mm_x"))  { r.paramValue = config_.stepsPerMmX; r.hasParamValue = true; }
            else if (PNP_STREQ(k, "steps_per_mm_y")  ||
                     PNP_STREQ(k, "steps_per_mm_y1") ||
                     PNP_STREQ(k, "steps_per_mm_y2")) { r.paramValue = config_.stepsPerMmY; r.hasParamValue = true; }
            else if (PNP_STREQ(k, "steps_per_mm_z"))  { r.paramValue = config_.stepsPerMmZ; r.hasParamValue = true; }
            else if (PNP_STREQ(k, "tof_max_sigma_mm"))    { r.paramValue = (float)config_.tofMaxSigmaMm;    r.hasParamValue = true; }
            else if (PNP_STREQ(k, "tof_min_signal_kcps")) { r.paramValue = (float)config_.tofMinSignalKcps; r.hasParamValue = true; }
            else if (PNP_STREQ(k, "max_travel_mm_x")) { r.paramValue = config_.maxTravelMmX; r.hasParamValue = true; }
            else if (PNP_STREQ(k, "max_travel_mm_y")) { r.paramValue = config_.maxTravelMmY; r.hasParamValue = true; }
            else if (PNP_STREQ(k, "max_travel_mm_z")) { r.paramValue = config_.maxTravelMmZ; r.hasParamValue = true; }
            else if (k[0]=='t'&&k[1]=='o'&&k[2]=='f'&&k[3]=='_'
                     &&k[4]=='o'&&k[5]=='f'&&k[6]=='f'&&k[7]=='s'
                     &&k[10]=='_'&&k[11]>='0'&&k[11]<='3') {
                uint8_t ch = (uint8_t)(k[11] - '0');
                r.paramValue = config_.tofOffsetMm[ch]; r.hasParamValue = true;
            }
            return r;
        }
        return ack(cmd);
    }

    uint8_t allowed = allowedStates(name);
    if (allowed == 0) return nack(cmd, "unknown_command");

    if (!(allowed & stbit(state_))) {
        const char* reason = (state_ == State::Estopped)    ? "estop_active"
                           : (state_ == State::Faulted)     ? "hw_fault"
                           : (state_ == State::Calibrating) ? "calibrating"
                                                            : "not_ready";
        return nack(cmd, reason);
    }

    // Transfer commands
    if (PNP_STREQ(name, "begin_transfer") ||
        PNP_STREQ(name, "program_chunk")  ||
        PNP_STREQ(name, "end_transfer"))
        return handleTransferCommand(cmd);

    if (PNP_STREQ(name, "home"))         { enterHoming(nowMs); return ack(cmd); }
    if (PNP_STREQ(name, "run_program")) {
        if (!store_.programLoaded()) return nack(cmd, "no_program");
        abortFlags_.stop = false; abortFlags_.pause = false;
        interp_.load(store_.program());
        interp_.setTravelLimits(config_.travelLimits());   // enforce envelope this run
        state_ = State::Running;
        return ack(cmd);
    }
    if (PNP_STREQ(name, "pause"))        { abortFlags_.pause = true;  state_ = State::Paused;  return ack(cmd); }
    if (PNP_STREQ(name, "resume"))       { abortFlags_.pause = false; state_ = State::Running; return ack(cmd); }
    if (PNP_STREQ(name, "reset_fault"))  { fault_ = nullptr; state_ = State::Idle; return ack(cmd); }
    if (PNP_STREQ(name, "reset_estop")) {
        if (estopHw_) return nack(cmd, "hw_fault");
        fault_ = nullptr; state_ = State::Idle; return ack(cmd);
    }

    // Calibration commands (jog-and-measure)
    if (PNP_STREQ(name, "calibrate_axis")) {
        if (cmd.calAxis == CalAxis::Invalid) return nack(cmd, "invalid_axis");
        calAxis_     = cmd.calAxis;
        calJogSteps_ = 0;
        state_       = State::Calibrating;
        return ack(cmd);
    }
    if (PNP_STREQ(name, "cal_jog")) {
        // Move the axis under calibration by a raw step count and accumulate.
        OpResult r = machine_.jogAxisSteps(calAxisName(calAxis_), cmd.steps);
        if (r != OpResult::Ok) { injectFault("cal_jog_failed"); return nack(cmd, "cal_jog_failed"); }
        calJogSteps_ += cmd.steps;
        return ack(cmd);
    }
    if (PNP_STREQ(name, "cancel_calibration")) {
        // Abandon the in-progress calibration: discard accumulated jog, leave
        // the stored steps/mm untouched, return to Idle. No config write.
        calJogSteps_ = 0;
        calAxis_     = CalAxis::Invalid;
        state_       = State::Idle;
        return ack(cmd);
    }
    if (PNP_STREQ(name, "set_cal_distance")) {
        int32_t net = calJogSteps_ < 0 ? -calJogSteps_ : calJogSteps_;
        if (net == 0)              return nack(cmd, "no_jog_steps");
        if (cmd.mm <= 0.0f)        return nack(cmd, "invalid_distance");
        float val = (float)net / cmd.mm;
        switch (calAxis_) {
            case CalAxis::X:  config_.stepsPerMmX = val; break;
            case CalAxis::Y:  config_.stepsPerMmY = val; break;
            case CalAxis::Z:  config_.stepsPerMmZ = val; break;
            default: break;
        }
        config_.save();
        calJogSteps_ = 0;
        calAxis_     = CalAxis::Invalid;
        state_ = State::Idle;
        return ack(cmd);
    }

    if (PNP_STREQ(name, "set_max_travel")) {
        // Travel limits are per physical AXIS. Y is the single dual-Y envelope.
        if (cmd.calAxis == CalAxis::Invalid) return nack(cmd, "invalid_axis");
        if (cmd.mm <= 0.0f)                  return nack(cmd, "invalid_travel");
        switch (cmd.calAxis) {
            case CalAxis::X:  config_.maxTravelMmX = cmd.mm; break;
            case CalAxis::Y:  config_.maxTravelMmY = cmd.mm; break;
            case CalAxis::Z:  config_.maxTravelMmZ = cmd.mm; break;
            default:          return nack(cmd, "invalid_axis");
        }
        config_.save();
        return ack(cmd);
    }

    if (PNP_STREQ(name, "set_tof_threshold")) {
        // Live ToF confidence tuning, persisted to EEPROM so it survives reboots
        // and is re-applied at boot (headless).
        const char* k = cmd.paramKey;
        if (cmd.mm < 0.0f) return nack(cmd, "invalid_value");
        uint16_t v = (uint16_t)cmd.mm;
        if      (PNP_STREQ(k, "tof_max_sigma_mm"))    config_.tofMaxSigmaMm    = v;
        else if (PNP_STREQ(k, "tof_min_signal_kcps")) config_.tofMinSignalKcps = v;
        else return nack(cmd, "unknown_key");
        config_.save();
        machine_.setTofThresholds(config_.tofMaxSigmaMm, config_.tofMinSignalKcps);
        return ack(cmd);
    }

    if (PNP_STREQ(name, "calibrate_sensors")) {
        Response r = ack(cmd);
        r.hasTofOffsets = true;
        for (uint8_t ch = 0; ch < 4; ch++) {
            float mm = 0.0f;
            machine_.readDistanceMm(ch, mm);
            config_.tofOffsetMm[ch] = mm;
            r.tofOffsets[ch] = mm;
        }
        config_.save();
        return r;
    }

    if (PNP_STREQ(name, "query_sensors")) {
        // Live ToF read of all mux channels for the GUI readout. Channels with
        // no sensor (or an invalid range) report valid=false.
        Response r = ack(cmd);
        r.hasTofReadings = true;
        for (uint8_t ch = 0; ch < 6; ch++) {
            float mm = -1.0f;
            machine_.readDistanceMm(ch, mm);
            r.tofDistMm[ch] = mm;
            r.tofValid[ch]  = (mm >= 0.0f);
        }
        return r;
    }

    if (PNP_STREQ(name, "set_output")) {
        machine_.setOutput(cmd.output, cmd.state);
        if      (PNP_STREQ(cmd.output, "pump"))  pump_  = cmd.state;
        else if (PNP_STREQ(cmd.output, "valve")) valve_ = cmd.state;
        return ack(cmd);
    }

    if (PNP_STREQ(name, "set_servo")) {
        // 2-position servos: open/press -> true, closed/release -> false.
        const bool on = PNP_STREQ(cmd.position, "open") ||
                        PNP_STREQ(cmd.position, "press");
        if (PNP_STREQ(cmd.servo, "door")) {
            machine_.setOutput("servo_door", on);
            servoDoor_ = on ? "open" : "closed";
        } else if (PNP_STREQ(cmd.servo, "laser_btn")) {
            machine_.setOutput("servo_laser_btn", on);
            servoLaserBtn_ = on ? "press" : "release";
        } else {
            return nack(cmd, "unknown_servo");
        }
        return ack(cmd);
    }

    return ack(cmd);  // gated but not yet implemented
}

// ============================================================
// tick
// ============================================================

void StateMachine::tick(uint32_t nowMs) {
    if (state_ == State::Homing &&
        (int32_t)(nowMs - homingDeadline_) >= 0)
        state_ = State::Ready;

    if (state_ == State::Running) {
        OpResult r = interp_.run();
        if      (r == OpResult::Ok)      state_ = State::Idle;
        else if (r == OpResult::Aborted) { fault_ = "estop_triggered"; state_ = State::Estopped; }
        else                             { fault_ = interp_.faultReason(); state_ = State::Faulted; }
    }
}

// ============================================================
// pressButton / setEstopHardware / injectFault / buildStatus
// ============================================================

void StateMachine::pressButton(const char* button, uint32_t nowMs) {
    if (PNP_STREQ(button, "start")) {
        if      (state_ == State::Idle)   enterHoming(nowMs);
        else if (state_ == State::Ready && store_.programLoaded()) state_ = State::Running;
        else if (state_ == State::Paused) { abortFlags_.pause = false; state_ = State::Running; }
    } else if (PNP_STREQ(button, "pause")) {
        if      (state_ == State::Running)     { abortFlags_.pause = true; state_ = State::Paused; }
        else if (state_ == State::Faulted)     { fault_ = nullptr; state_ = State::Idle; }
    }
}

void StateMachine::setEstopHardware(bool active) {
    estopHw_ = active;
    if (active) { abortFlags_.stop = true; fault_ = "estop_triggered"; state_ = State::Estopped; }
    else if (state_ == State::Estopped) { abortFlags_.stop = false; fault_ = nullptr; state_ = State::Idle; }
}

void StateMachine::injectFault(const char* reason) {
    if (state_ == State::Estopped) return;
    fault_ = reason;
    state_ = State::Faulted;
}

StatusSnapshot StateMachine::buildStatus() const {
    bool calibrating = (state_ == State::Calibrating);
    return StatusSnapshot{
        state_, store_.programLoaded(), fault_,
        true, false, false, estopHw_,
        calibrating ? calAxisName(calAxis_) : (const char*)nullptr,
        calSteps(),
        pump_, valve_, servoDoor_, servoLaserBtn_,
        startBtn_, pauseBtn_,
    };
}

void StateMachine::readTof(float dist[6], bool valid[6]) {
    for (uint8_t ch = 0; ch < 6; ch++) {
        float mm = -1.0f;
        machine_.readDistanceMm(ch, mm);
        dist[ch]  = mm;
        valid[ch] = (mm >= 0.0f);
    }
}

bool StateMachine::isDuplicateCommand(int32_t id) const {
    if (id <= 0) return false;
    for (uint8_t i = 0; i < 16; i++) if (recentIds_[i] == id) return true;
    return false;
}

void StateMachine::rememberCommand(int32_t id) {
    if (id <= 0) return;
    recentIds_[recentHead_] = id;
    recentHead_ = (uint8_t)((recentHead_ + 1) % 16);
}

}  // namespace pnp