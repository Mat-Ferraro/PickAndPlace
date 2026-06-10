// Host-based Unity tests for StateMachine.
// These are the C++ counterparts of the simulator state-machine tests.

#include "unity.h"
#include "core/StateMachine.h"
#include "core/../config/Config.h"
#include "MockMachine.h"
#include <string.h>
#include <stdio.h>

// Base64 encoding helper for transfer tests (host only).
#include <stdint.h>
static void b64Encode(const uint8_t* in, size_t inLen, char* out) {
    static const char* t = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t wi = 0;
    for (size_t i = 0; i < inLen; i += 3) {
        uint32_t v = (uint32_t)in[i] << 16;
        if (i+1 < inLen) v |= (uint32_t)in[i+1] << 8;
        if (i+2 < inLen) v |= in[i+2];
        out[wi++] = t[(v>>18)&63];
        out[wi++] = t[(v>>12)&63];
        out[wi++] = (i+1 < inLen) ? t[(v>>6)&63]  : '=';
        out[wi++] = (i+2 < inLen) ? t[v&63]        : '=';
    }
    out[wi] = '\0';
}

using namespace pnp;

static MockMachine*  mm;
static pnp::Config*  cfg;
static StateMachine* sm;

void setUp(void) {
    pnp::Config::clearTestEeprom();
    mm  = new MockMachine();
    cfg = new pnp::Config();
    sm  = new StateMachine(*mm, *cfg);
}
void tearDown(void) { delete sm; delete cfg; delete mm; }

static Command cmd(const char* name, int32_t id = 1) {
    Command c; c.name = name; c.id = id; return c;
}

// Helper: do a full valid transfer of a minimal program.
// Returns the end_transfer response.
static const char* kMinimalJson =
    "{\"version\":1,\"program\":[{\"op\":\"HALT\"}]}";

static Response doTransfer(const char* json = kMinimalJson) {
    size_t len = strlen(json);
    char b64[2048];
    b64Encode((const uint8_t*)json, len, b64);

    Command begin = cmd("begin_transfer");
    begin.size = (uint32_t)len; begin.chunks = 1;
    sm->handleCommand(begin, 0);

    Command chunk = cmd("program_chunk");
    chunk.index = 0; chunk.data = b64;
    sm->handleCommand(chunk, 0);

    return sm->handleCommand(cmd("end_transfer"), 0);
}

// ============================================================
// Command gating
// ============================================================

void test_run_program_in_idle_is_rejected_not_ready(void) {
    Response r = sm->handleCommand(cmd("run_program"), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("not_ready", r.reason);
    TEST_ASSERT_TRUE(State::Idle == sm->state());
}

void test_unknown_command_is_nacked(void) {
    Response r = sm->handleCommand(cmd("frobnicate"), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("unknown_command", r.reason);
}

// ============================================================
// Homing
// ============================================================

void test_home_enters_homing_and_requests_home_on_machine(void) {
    Response r = sm->handleCommand(cmd("home"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(State::Homing == sm->state());
    TEST_ASSERT_EQUAL(1u, mm->homes.size());
    TEST_ASSERT_EQUAL_STRING("XYZ", mm->homes[0].c_str());
}

void test_homing_completes_after_deadline(void) {
    sm->handleCommand(cmd("home"), 0);
    sm->tick(StateMachine::kHomingMs - 1);
    TEST_ASSERT_TRUE(State::Homing == sm->state());
    sm->tick(StateMachine::kHomingMs);
    TEST_ASSERT_TRUE(State::Ready == sm->state());
}

// ============================================================
// run_program requires loaded program
// ============================================================

void test_run_program_requires_loaded_program(void) {
    sm->handleCommand(cmd("home"), 0);
    sm->tick(StateMachine::kHomingMs);   // -> READY

    Response r = sm->handleCommand(cmd("run_program"), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("no_program", r.reason);

    // Load a valid program via transfer, then run.
    doTransfer();
    r = sm->handleCommand(cmd("run_program"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    // Interpreter runs HALT immediately -> back to Idle after tick.
    sm->tick(0);
    TEST_ASSERT_TRUE(State::Idle == sm->state());
}

// ============================================================
// E-stop
// ============================================================

void test_estop_dominates_from_any_state(void) {
    sm->handleCommand(cmd("home"), 0);
    Response r = sm->handleCommand(cmd("estop"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(State::Estopped == sm->state());
    TEST_ASSERT_EQUAL_STRING("estop_triggered", sm->fault());
}

void test_estop_release_returns_to_idle(void) {
    sm->handleCommand(cmd("estop"), 0);
    sm->setEstopHardware(false);
    TEST_ASSERT_TRUE(State::Idle == sm->state());
    TEST_ASSERT_NULL(sm->fault());
}

void test_reset_estop_refused_while_latch_engaged(void) {
    sm->handleCommand(cmd("estop"), 0);
    Response r = sm->handleCommand(cmd("reset_estop"), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("hw_fault", r.reason);
}

// ============================================================
// Physical buttons
// ============================================================

void test_physical_start_button_homes_from_idle(void) {
    sm->pressButton("start", 0);
    TEST_ASSERT_TRUE(State::Homing == sm->state());
}

void test_physical_pause_button_clears_fault(void) {
    sm->injectFault("motion_fault");
    sm->pressButton("pause", 0);
    TEST_ASSERT_TRUE(State::Idle == sm->state());
    TEST_ASSERT_NULL(sm->fault());
}

// ============================================================
// Fault injection
// ============================================================

void test_injected_jam_faults_with_reason(void) {
    sm->injectFault("motion_fault");
    TEST_ASSERT_TRUE(State::Faulted == sm->state());
    TEST_ASSERT_EQUAL_STRING("motion_fault", sm->fault());
}

// ============================================================
// Status
// ============================================================

void test_status_reflects_state_and_program(void) {
    doTransfer();
    StatusSnapshot s = sm->buildStatus();
    TEST_ASSERT_TRUE(State::Idle == s.state);
    TEST_ASSERT_TRUE(s.programLoaded);
    TEST_ASSERT_FALSE(s.estopHw);
}

// ============================================================
// Chunked transfer
// ============================================================

void test_begin_rejects_zero_size(void) {
    Command c = cmd("begin_transfer"); c.size = 0; c.chunks = 1;
    Response r = sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("invalid_param", r.reason);
}

void test_begin_rejects_zero_chunks(void) {
    Command c = cmd("begin_transfer"); c.size = 10; c.chunks = 0;
    Response r = sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("invalid_param", r.reason);
}

void test_begin_valid_acks(void) {
    Command c = cmd("begin_transfer"); c.size = 10; c.chunks = 2;
    Response r = sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
}

void test_chunk_without_transfer_is_nacked(void) {
    Command c = cmd("program_chunk"); c.index = 0; c.data = "YWJj";
    Response r = sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("no_transfer_in_progress", r.reason);
}

void test_chunk_out_of_order_is_nacked(void) {
    Command begin = cmd("begin_transfer"); begin.size = 6; begin.chunks = 2;
    sm->handleCommand(begin, 0);
    Command chunk = cmd("program_chunk"); chunk.index = 1; chunk.data = "YWJj";
    Response r = sm->handleCommand(chunk, 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_TRUE(strstr(r.reason, "out_of_order") != nullptr);
}

void test_chunk_bad_base64_is_nacked(void) {
    Command begin = cmd("begin_transfer"); begin.size = 6; begin.chunks = 1;
    sm->handleCommand(begin, 0);
    Command chunk = cmd("program_chunk"); chunk.index = 0; chunk.data = "!!!";
    Response r = sm->handleCommand(chunk, 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("bad_base64", r.reason);
}

void test_end_without_transfer_is_nacked(void) {
    Response r = sm->handleCommand(cmd("end_transfer"), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("no_transfer_in_progress", r.reason);
}

void test_end_with_missing_chunks_is_nacked(void) {
    Command begin = cmd("begin_transfer"); begin.size = 6; begin.chunks = 2;
    sm->handleCommand(begin, 0);
    Command chunk = cmd("program_chunk"); chunk.index = 0; chunk.data = "YWJj";
    sm->handleCommand(chunk, 0);   // only 1 of 2
    Response r = sm->handleCommand(cmd("end_transfer"), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_TRUE(strstr(r.reason, "incomplete") != nullptr);
}

void test_end_with_invalid_json_is_nacked(void) {
    const char* bad = "{not valid json";
    size_t len = strlen(bad);
    char b64[64]; b64Encode((const uint8_t*)bad, len, b64);
    Command begin = cmd("begin_transfer"); begin.size = (uint32_t)len; begin.chunks = 1;
    sm->handleCommand(begin, 0);
    Command chunk = cmd("program_chunk"); chunk.index = 0; chunk.data = b64;
    sm->handleCommand(chunk, 0);
    Response r = sm->handleCommand(cmd("end_transfer"), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_TRUE(strstr(r.reason, "json_error") != nullptr);
}

void test_full_transfer_happy_path_loads_program(void) {
    Response r = doTransfer();
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_EQUAL_STRING("load_program", r.cmd);
    TEST_ASSERT_TRUE(sm->programLoaded());
}

void test_full_transfer_in_multiple_chunks(void) {
    const char* json = kMinimalJson;
    size_t len = strlen(json);
    size_t mid = len / 2;

    char b64a[256]; b64Encode((const uint8_t*)json,       mid,     b64a);
    char b64b[256]; b64Encode((const uint8_t*)json + mid, len-mid, b64b);

    Command begin = cmd("begin_transfer"); begin.size = (uint32_t)len; begin.chunks = 2;
    sm->handleCommand(begin, 0);
    Command c0 = cmd("program_chunk"); c0.index = 0; c0.data = b64a;
    Command c1 = cmd("program_chunk"); c1.index = 1; c1.data = b64b;
    sm->handleCommand(c0, 0);
    sm->handleCommand(c1, 0);
    Response r = sm->handleCommand(cmd("end_transfer"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(sm->programLoaded());
}

// ============================================================
// main
// ============================================================

// ============================================================
// Calibration
// ============================================================

static Command calCmd(const char* name, char axis = 'X', float distMm = 0.0f) {
    Command c = cmd(name);
    c.calAxis   = axis;
    c.calDistMm = distMm;
    return c;
}

void test_calibrate_axis_enters_calibrating_state(void) {
    Response r = sm->handleCommand(calCmd("calibrate_axis", 'X'), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(State::Calibrating == sm->state());
}

void test_calibrate_axis_rejected_when_homing(void) {
    sm->handleCommand(cmd("home"), 0);   // → Homing
    Response r = sm->handleCommand(calCmd("calibrate_axis", 'X'), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_TRUE(State::Homing == sm->state());
}

void test_calibrate_axis_rejected_with_invalid_axis(void) {
    Response r = sm->handleCommand(calCmd("calibrate_axis", 'Q'), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("invalid_axis", r.reason);
}

void test_tick_drives_traverse_and_stores_steps(void) {
    mm->traverseSteps = 12800;
    sm->handleCommand(calCmd("calibrate_axis", 'X'), 0);
    sm->tick(0);
    TEST_ASSERT_TRUE(sm->calTraverseDone());
    TEST_ASSERT_EQUAL(12800u, sm->calSteps());
    // Still Calibrating — waiting for set_cal_distance.
    TEST_ASSERT_TRUE(State::Calibrating == sm->state());
    // Traverse was called with the right axis.
    TEST_ASSERT_EQUAL(1u, mm->traversals.size());
    TEST_ASSERT_EQUAL('X', mm->traversals[0].axis);
}

void test_set_cal_distance_computes_steps_per_mm(void) {
    mm->traverseSteps = 12800;
    sm->handleCommand(calCmd("calibrate_axis", 'X'), 0);
    sm->tick(0);   // traverse completes

    Response r = sm->handleCommand(calCmd("set_cal_distance", 'X', 160.0f), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(State::Idle == sm->state());
    // 12800 steps / 160 mm = 80.0 steps/mm
    TEST_ASSERT_EQUAL_FLOAT(80.0f, sm->stepsPerMm('X'));
}

void test_set_cal_distance_rejected_before_traverse(void) {
    sm->handleCommand(calCmd("calibrate_axis", 'X'), 0);
    // tick() NOT called — traverse not done yet
    Response r = sm->handleCommand(calCmd("set_cal_distance", 'X', 160.0f), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("traverse_not_done", r.reason);
}

void test_set_cal_distance_rejected_outside_calibrating(void) {
    // Never started calibration
    Response r = sm->handleCommand(calCmd("set_cal_distance", 'X', 160.0f), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
}

void test_calibrate_z_axis_independently(void) {
    mm->traverseSteps = 6400;
    sm->handleCommand(calCmd("calibrate_axis", 'Z'), 0);
    sm->tick(0);
    sm->handleCommand(calCmd("set_cal_distance", 'Z', 200.0f), 0);
    // 6400 / 200 = 32.0 steps/mm
    TEST_ASSERT_EQUAL_FLOAT(32.0f, sm->stepsPerMm('Z'));
    // X unchanged
    TEST_ASSERT_EQUAL_FLOAT(0.0f, sm->stepsPerMm('X'));
}

void test_traverse_fault_enters_faulted_state(void) {
    // We can simulate a fault by injecting it directly — traverseToStop
    // returning Faulted is tested here by injecting a fault after tick.
    // Full HAL fault path is validated on the bench.
    sm->handleCommand(calCmd("calibrate_axis", 'X'), 0);
    sm->injectFault("cal_traverse_failed");
    TEST_ASSERT_TRUE(State::Faulted == sm->state());
    TEST_ASSERT_EQUAL_STRING("cal_traverse_failed", sm->fault());
}
int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_run_program_in_idle_is_rejected_not_ready);
    RUN_TEST(test_unknown_command_is_nacked);
    RUN_TEST(test_home_enters_homing_and_requests_home_on_machine);
    RUN_TEST(test_homing_completes_after_deadline);
    RUN_TEST(test_run_program_requires_loaded_program);
    RUN_TEST(test_estop_dominates_from_any_state);
    RUN_TEST(test_estop_release_returns_to_idle);
    RUN_TEST(test_reset_estop_refused_while_latch_engaged);
    RUN_TEST(test_physical_start_button_homes_from_idle);
    RUN_TEST(test_physical_pause_button_clears_fault);
    RUN_TEST(test_injected_jam_faults_with_reason);
    RUN_TEST(test_status_reflects_state_and_program);
    // Transfer
    RUN_TEST(test_begin_rejects_zero_size);
    RUN_TEST(test_begin_rejects_zero_chunks);
    RUN_TEST(test_begin_valid_acks);
    RUN_TEST(test_chunk_without_transfer_is_nacked);
    RUN_TEST(test_chunk_out_of_order_is_nacked);
    RUN_TEST(test_chunk_bad_base64_is_nacked);
    RUN_TEST(test_end_without_transfer_is_nacked);
    RUN_TEST(test_end_with_missing_chunks_is_nacked);
    RUN_TEST(test_end_with_invalid_json_is_nacked);
    RUN_TEST(test_full_transfer_happy_path_loads_program);
    RUN_TEST(test_full_transfer_in_multiple_chunks);
    // Calibration
    RUN_TEST(test_calibrate_axis_enters_calibrating_state);
    RUN_TEST(test_calibrate_axis_rejected_when_homing);
    RUN_TEST(test_calibrate_axis_rejected_with_invalid_axis);
    RUN_TEST(test_tick_drives_traverse_and_stores_steps);
    RUN_TEST(test_set_cal_distance_computes_steps_per_mm);
    RUN_TEST(test_set_cal_distance_rejected_before_traverse);
    RUN_TEST(test_set_cal_distance_rejected_outside_calibrating);
    RUN_TEST(test_calibrate_z_axis_independently);
    RUN_TEST(test_traverse_fault_enters_faulted_state);
    return UNITY_END();
}
