#include "StateMachine.h"
#include "../platform/Platform.h"
#include <string.h>
#include <stdio.h>

// All string literals go through PNP_STREQ / PNP_SNPRINTF so they live in
// flash (PROGMEM) on AVR instead of being copied into SRAM at startup.

namespace pnp {

// ============================================================
// Command gating — PNP_STREQ chain replaces the kGates table.
// Every literal stays in flash on AVR; no pointer-to-string table in SRAM.
// ============================================================

static uint8_t allowedStates(const char* name) {
    if (PNP_STREQ(name, "home"))           return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "load_program"))   return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "run_program"))    return stbit(State::Ready);
    if (PNP_STREQ(name, "pause"))          return stbit(State::Running);
    if (PNP_STREQ(name, "resume"))         return stbit(State::Paused);
    if (PNP_STREQ(name, "reset_fault"))    return stbit(State::Faulted);
    if (PNP_STREQ(name, "reset_estop"))    return stbit(State::Estopped);
    if (PNP_STREQ(name, "jog"))            return stbit(State::Ready);
    if (PNP_STREQ(name, "teach_position")) return stbit(State::Ready);
    if (PNP_STREQ(name, "move_to"))        return stbit(State::Ready);
    if (PNP_STREQ(name, "query_position")) return uint8_t(stbit(State::Idle)|stbit(State::Ready)|
                                                           stbit(State::Faulted)|stbit(State::Estopped));
    if (PNP_STREQ(name, "save_position"))  return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "set_param"))      return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "save_config"))    return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "load_config"))    return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "begin_transfer")) return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "program_chunk"))  return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "end_transfer"))   return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "set_output"))     return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "set_servo"))      return uint8_t(stbit(State::Idle)|stbit(State::Ready));
    if (PNP_STREQ(name, "query_sensors"))  return uint8_t(stbit(State::Idle)|stbit(State::Ready)|
                                                           stbit(State::Running)|stbit(State::Paused)|
                                                           stbit(State::Faulted)|stbit(State::Estopped));
    return 0;  // unknown
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
            case ProgramStore::Result::NoTransferInProgress:
                return nack(cmd, "no_transfer_in_progress");
            case ProgramStore::Result::BadBase64:
                return nack(cmd, "bad_base64");
            case ProgramStore::Result::BufferFull:
                return nack(cmd, "buffer_full");
            case ProgramStore::Result::OutOfOrder:
                PNP_SNPRINTF(xferErr_, sizeof(xferErr_),
                             "out_of_order_expected_%u",
                             (unsigned)store_.xferReceived());
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

    if (PNP_STREQ(name, "estop")) {
        setEstopHardware(true);
        return ack(cmd);
    }

    if (isAlwaysAccept(name)) return ack(cmd);

    uint8_t allowed = allowedStates(name);
    if (allowed == 0) return nack(cmd, "unknown_command");

    if (!(allowed & stbit(state_))) {
        const char* reason = (state_ == State::Estopped) ? "estop_active"
                           : (state_ == State::Faulted)  ? "hw_fault"
                                                         : "not_ready";
        return nack(cmd, reason);
    }

    if (PNP_STREQ(name, "begin_transfer") ||
        PNP_STREQ(name, "program_chunk")  ||
        PNP_STREQ(name, "end_transfer"))
        return handleTransferCommand(cmd);

    if (PNP_STREQ(name, "home")) { enterHoming(nowMs); return ack(cmd); }

    if (PNP_STREQ(name, "run_program")) {
        if (!store_.programLoaded()) return nack(cmd, "no_program");
        abortFlags_.stop  = false;
        abortFlags_.pause = false;
        interp_.load(store_.program());
        state_ = State::Running;
        return ack(cmd);
    }
    if (PNP_STREQ(name, "pause"))  { abortFlags_.pause = true;  state_ = State::Paused;  return ack(cmd); }
    if (PNP_STREQ(name, "resume")) { abortFlags_.pause = false; state_ = State::Running; return ack(cmd); }
    if (PNP_STREQ(name, "reset_fault")) { fault_ = nullptr; state_ = State::Idle; return ack(cmd); }
    if (PNP_STREQ(name, "reset_estop")) {
        if (estopHw_) return nack(cmd, "hw_fault");
        fault_ = nullptr; state_ = State::Idle; return ack(cmd);
    }

    return ack(cmd);  // gated but not yet implemented
}

// ============================================================
// tick
// ============================================================

void StateMachine::tick(uint32_t nowMs) {
    if (state_ == State::Homing && (int32_t)(nowMs - homingDeadline_) >= 0)
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
        if      (state_ == State::Running) { abortFlags_.pause = true; state_ = State::Paused; }
        else if (state_ == State::Faulted) { fault_ = nullptr; state_ = State::Idle; }
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
    return StatusSnapshot{state_, store_.programLoaded(), fault_, true, false, false, estopHw_};
}

}  // namespace pnp
