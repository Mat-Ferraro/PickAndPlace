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

static Command calCmd(const char* name, CalAxis axis = CalAxis::X, float distMm = 0.0f) {
    Command c = cmd(name);
    c.calAxis = axis;
    c.mm      = distMm;
    return c;
}
static Command jogCmd(CalAxis axis, int32_t steps) {
    Command c = cmd("cal_jog");
    c.calAxis = axis;
    c.steps   = steps;
    return c;
}

void test_calibrate_axis_enters_calibrating_state(void) {
    Response r = sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(State::Calibrating == sm->state());
}

void test_calibrate_axis_rejected_when_homing(void) {
    sm->handleCommand(cmd("home"), 0);   // → Homing
    Response r = sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_TRUE(State::Homing == sm->state());
}

void test_calibrate_axis_rejected_with_invalid_axis(void) {
    Command c = cmd("calibrate_axis"); c.calAxis = CalAxis::Invalid;
    Response r = sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("invalid_axis", r.reason);
}

void test_cal_jog_accumulates_steps(void) {
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    sm->handleCommand(jogCmd(CalAxis::X, 8000), 0);
    sm->handleCommand(jogCmd(CalAxis::X, 4800), 0);
    TEST_ASSERT_EQUAL(12800u, sm->calSteps());
    // Stays Calibrating — waiting for set_cal_distance.
    TEST_ASSERT_TRUE(State::Calibrating == sm->state());
    // Jog routed to the axis under calibration, in order.
    TEST_ASSERT_EQUAL(2u, mm->jogs.size());
    TEST_ASSERT_EQUAL_STRING("X", mm->jogs[0].axis.c_str());
    TEST_ASSERT_EQUAL(8000, mm->jogs[0].steps);
}

void test_cal_jog_accumulates_signed_net(void) {
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    sm->handleCommand(jogCmd(CalAxis::X, 1000), 0);
    sm->handleCommand(jogCmd(CalAxis::X, -200), 0);   // back off
    TEST_ASSERT_EQUAL(800u, sm->calSteps());          // net magnitude
}

void test_cal_jog_rejected_outside_calibrating(void) {
    // Never started calibration.
    Response r = sm->handleCommand(jogCmd(CalAxis::X, 1000), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL(0u, mm->jogs.size());
}

void test_set_cal_distance_computes_steps_per_mm(void) {
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    sm->handleCommand(jogCmd(CalAxis::X, 12800), 0);

    Response r = sm->handleCommand(calCmd("set_cal_distance", CalAxis::X, 160.0f), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(State::Idle == sm->state());
    // 12800 steps / 160 mm = 80.0 steps/mm
    TEST_ASSERT_EQUAL_FLOAT(80.0f, sm->stepsPerMm(CalAxis::X));
}

void test_set_cal_distance_clears_accumulator(void) {
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    sm->handleCommand(jogCmd(CalAxis::X, 12800), 0);
    sm->handleCommand(calCmd("set_cal_distance", CalAxis::X, 160.0f), 0);
    // After applying, the jog accumulator resets so status no longer reports it.
    TEST_ASSERT_EQUAL(0u, sm->calSteps());
}

void test_set_cal_distance_rejected_before_jog(void) {
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    // no cal_jog yet -> nothing to divide
    Response r = sm->handleCommand(calCmd("set_cal_distance", CalAxis::X, 160.0f), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("no_jog_steps", r.reason);
}

void test_set_cal_distance_rejected_outside_calibrating(void) {
    // Never started calibration
    Response r = sm->handleCommand(calCmd("set_cal_distance", CalAxis::X, 160.0f), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
}

void test_calibrate_z_axis_independently(void) {
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::Z), 0);
    sm->handleCommand(jogCmd(CalAxis::Z, 6400), 0);
    sm->handleCommand(calCmd("set_cal_distance", CalAxis::Z, 200.0f), 0);
    // 6400 / 200 = 32.0 steps/mm
    TEST_ASSERT_EQUAL_FLOAT(32.0f, sm->stepsPerMm(CalAxis::Z));
    // X unchanged
    TEST_ASSERT_EQUAL_FLOAT(0.0f, sm->stepsPerMm(CalAxis::X));
}

void test_cal_jog_fault_enters_faulted_state(void) {
    mm->jogResult = OpResult::Faulted;   // force the HAL jog to fail
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    Response r = sm->handleCommand(jogCmd(CalAxis::X, 1000), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("cal_jog_failed", r.reason);
    TEST_ASSERT_TRUE(State::Faulted == sm->state());
    TEST_ASSERT_EQUAL_STRING("cal_jog_failed", sm->fault());
}
// ============================================================
// calibrate_sensors and get_param
// ============================================================

static Command getParam(const char* key, int32_t id = 1) {
    Command c; c.name = "get_param"; c.id = id; c.paramKey = key; return c;
}

void test_calibrate_sensors_stores_readings_to_config(void) {
    mm->tofReadings[0] = 45.0f; mm->tofReadings[1] = 47.0f;
    mm->tofReadings[2] = 46.0f; mm->tofReadings[3] = 48.0f;
    Response r = sm->handleCommand(cmd("calibrate_sensors"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(r.hasTofOffsets);
    TEST_ASSERT_EQUAL_FLOAT(45.0f, r.tofOffsets[0]);
    TEST_ASSERT_EQUAL_FLOAT(47.0f, r.tofOffsets[1]);
    TEST_ASSERT_EQUAL_FLOAT(46.0f, r.tofOffsets[2]);
    TEST_ASSERT_EQUAL_FLOAT(48.0f, r.tofOffsets[3]);
    // Config should be updated
    TEST_ASSERT_EQUAL_FLOAT(45.0f, cfg->tofOffsetMm[0]);
    TEST_ASSERT_EQUAL_FLOAT(48.0f, cfg->tofOffsetMm[3]);
}

void test_calibrate_sensors_reads_all_four_channels(void) {
    sm->handleCommand(cmd("calibrate_sensors"), 0);
    TEST_ASSERT_EQUAL(4u, mm->distReads.size());
    // Channels 0-3 in order
    for (int i = 0; i < 4; i++)
        TEST_ASSERT_EQUAL(i, mm->distReads[i].channel);
}

void test_calibrate_sensors_saves_to_eeprom(void) {
    mm->tofReadings[0] = 50.0f;
    sm->handleCommand(cmd("calibrate_sensors"), 0);
    // Verify by loading a fresh Config from the fake EEPROM
    pnp::Config loaded;
    TEST_ASSERT_TRUE(loaded.load());
    TEST_ASSERT_EQUAL_FLOAT(50.0f, loaded.tofOffsetMm[0]);
}

void test_calibrate_sensors_rejected_in_running(void) {
    sm->handleCommand(cmd("home"), 0);
    sm->tick(StateMachine::kHomingMs);  // -> READY
    doTransfer();
    sm->handleCommand(cmd("run_program"), 0);  // -> RUNNING
    Response r = sm->handleCommand(cmd("calibrate_sensors"), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
}

void test_get_param_steps_per_mm_x(void) {
    cfg->stepsPerMmX = 80.0f;
    Response r = sm->handleCommand(getParam("steps_per_mm_x"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_FLOAT(80.0f, r.paramValue);
}

void test_get_param_steps_per_mm_y1(void) {
    cfg->stepsPerMmY = 32.0f;
    Response r = sm->handleCommand(getParam("steps_per_mm_y1"), 0);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_FLOAT(32.0f, r.paramValue);
}

void test_get_param_steps_per_mm_y2(void) {
    cfg->stepsPerMmY = 32.5f;
    Response r = sm->handleCommand(getParam("steps_per_mm_y2"), 0);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_FLOAT(32.5f, r.paramValue);
}

void test_get_param_tof_offset_0(void) {
    cfg->tofOffsetMm[0] = 45.5f;
    Response r = sm->handleCommand(getParam("tof_offset_0"), 0);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_FLOAT(45.5f, r.paramValue);
}

void test_get_param_tof_offset_3(void) {
    cfg->tofOffsetMm[3] = 48.0f;
    Response r = sm->handleCommand(getParam("tof_offset_3"), 0);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_FLOAT(48.0f, r.paramValue);
}

void test_get_param_uncalibrated_returns_negative(void) {
    // Default tofOffsetMm is -1.0f (uncalibrated sentinel)
    Response r = sm->handleCommand(getParam("tof_offset_2"), 0);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_FLOAT(-1.0f, r.paramValue);
}

void test_get_param_unknown_key_has_no_value(void) {
    Response r = sm->handleCommand(getParam("nonsense_key"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_FALSE(r.hasParamValue);
}

void test_set_output_pump_reflects_in_status(void) {
    Command c; c.name = "set_output"; c.output = "pump"; c.state = true;
    Response r = sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(sm->buildStatus().pump);
}

void test_set_servo_door_open_reflects_in_status(void) {
    Command c; c.name = "set_servo"; c.servo = "door"; c.position = "open";
    sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL_STRING("open", sm->buildStatus().servoDoor);
}

void test_set_servo_laser_press_reflects_in_status(void) {
    Command c; c.name = "set_servo"; c.servo = "laser_btn"; c.position = "press";
    sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL_STRING("press", sm->buildStatus().servoLaserBtn);
}

void test_set_servo_unknown_nacks(void) {
    Command c; c.name = "set_servo"; c.servo = "gizmo"; c.position = "open";
    Response r = sm->handleCommand(c, 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
}

void test_get_param_echoes_actual_key(void) {
    // Regression: the key field must be the param key, not the command name.
    cfg->stepsPerMmX = 80.0f;
    Response r = sm->handleCommand(getParam("steps_per_mm_x"), 0);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_STRING("steps_per_mm_x", r.paramKey);
}

void test_get_param_steps_per_mm_y(void) {
    cfg->stepsPerMmY = 33.0f;
    Response r = sm->handleCommand(getParam("steps_per_mm_y"), 0);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_FLOAT(33.0f, r.paramValue);
}

void test_get_param_max_travel_x(void) {
    cfg->maxTravelMmX = 250.0f;
    Response r = sm->handleCommand(getParam("max_travel_mm_x"), 0);
    TEST_ASSERT_TRUE(r.hasParamValue);
    TEST_ASSERT_EQUAL_FLOAT(250.0f, r.paramValue);
    TEST_ASSERT_EQUAL_STRING("max_travel_mm_x", r.paramKey);
}

void test_get_param_max_travel_y_and_z(void) {
    cfg->maxTravelMmY = 410.0f;
    cfg->maxTravelMmZ = 75.0f;
    Response ry = sm->handleCommand(getParam("max_travel_mm_y"), 0);
    Response rz = sm->handleCommand(getParam("max_travel_mm_z"), 0);
    TEST_ASSERT_EQUAL_FLOAT(410.0f, ry.paramValue);
    TEST_ASSERT_EQUAL_FLOAT(75.0f, rz.paramValue);
}


void test_calibrate_y_axis_stores_single_value(void) {
    // Calibrating Y jogs both gantry motors and stores ONE steps/mm.
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::Y), 0);
    sm->handleCommand(jogCmd(CalAxis::Y, 12750), 0);
    sm->handleCommand(calCmd("set_cal_distance", CalAxis::Y, 420.0f), 0);
    float expected = 12750.0f / 420.0f;   // ≈ 30.357
    TEST_ASSERT_EQUAL_FLOAT(expected, sm->stepsPerMm(CalAxis::Y));
}

void test_cancel_calibration_returns_to_idle_without_saving(void) {
    sm->handleCommand(calCmd("calibrate_axis", CalAxis::X), 0);
    sm->handleCommand(jogCmd(CalAxis::X, 5000), 0);
    Response r = sm->handleCommand(cmd("cancel_calibration"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(State::Idle == sm->state());
    // Stored steps/mm untouched (still uncalibrated).
    TEST_ASSERT_EQUAL_FLOAT(0.0f, sm->stepsPerMm(CalAxis::X));
    // A further cal_jog is now rejected (no longer Calibrating).
    Response r2 = sm->handleCommand(jogCmd(CalAxis::X, 100), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r2.kind);
}

void test_query_sensors_returns_six_tof_readings(void) {
    Response r = sm->handleCommand(cmd("query_sensors"), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_TRUE(r.hasTofReadings);
    // MockMachine reports channels 0-3 = {45,47,46,48} mm; the read flows through.
    TEST_ASSERT_EQUAL_FLOAT(45.0f, r.tofDistMm[0]);
    TEST_ASSERT_EQUAL_FLOAT(48.0f, r.tofDistMm[3]);
    TEST_ASSERT_TRUE(r.tofValid[0]);
    TEST_ASSERT_TRUE(r.tofValid[3]);
}

// ============================================================
// set_max_travel + soft-limit enforcement
// ============================================================

void test_set_max_travel_writes_x(void) {
    Response r = sm->handleCommand(calCmd("set_max_travel", CalAxis::X, 300.0f), 0);
    TEST_ASSERT_EQUAL(Response::Ack, r.kind);
    TEST_ASSERT_EQUAL_FLOAT(300.0f, cfg->maxTravelMmX);
}

void test_set_max_travel_y_sets_envelope(void) {
    sm->handleCommand(calCmd("set_max_travel", CalAxis::Y, 410.0f), 0);
    TEST_ASSERT_EQUAL_FLOAT(410.0f, cfg->maxTravelMmY);
}

void test_set_max_travel_rejects_invalid_axis(void) {
    Response r = sm->handleCommand(calCmd("set_max_travel", CalAxis::Invalid, 300.0f), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("invalid_axis", r.reason);
}

void test_set_max_travel_rejects_nonpositive(void) {
    Response r = sm->handleCommand(calCmd("set_max_travel", CalAxis::X, 0.0f), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
    TEST_ASSERT_EQUAL_STRING("invalid_travel", r.reason);
}

void test_set_max_travel_persists_to_eeprom(void) {
    sm->handleCommand(calCmd("set_max_travel", CalAxis::Z, 75.0f), 0);
    pnp::Config reloaded;
    TEST_ASSERT_TRUE(reloaded.load());
    TEST_ASSERT_EQUAL_FLOAT(75.0f, reloaded.maxTravelMmZ);
}

void test_set_max_travel_rejected_while_running(void) {
    sm->handleCommand(cmd("home"), 0);
    sm->tick(StateMachine::kHomingMs);   // -> READY
    doTransfer();
    sm->handleCommand(cmd("run_program"), 0);   // -> RUNNING
    Response r = sm->handleCommand(calCmd("set_max_travel", CalAxis::X, 300.0f), 0);
    TEST_ASSERT_EQUAL(Response::Nack, r.kind);
}

void test_program_move_outside_envelope_faults(void) {
    // Configure limits, then run a program whose MOVE exceeds X.
    cfg->maxTravelMmX = 300.0f;
    cfg->maxTravelMmY = 400.0f;
    cfg->maxTravelMmZ = 50.0f;
    sm->handleCommand(cmd("home"), 0);
    sm->tick(StateMachine::kHomingMs);   // -> READY
    doTransfer("{\"version\":1,\"program\":[{\"op\":\"MOVE\",\"x\":999,\"y\":0,\"z\":0}]}");
    sm->handleCommand(cmd("run_program"), 0);
    sm->tick(0);
    TEST_ASSERT_TRUE(State::Faulted == sm->state());
    TEST_ASSERT_EQUAL_STRING("soft_limit_x", sm->fault());
}

void test_program_move_inside_envelope_runs(void) {
    cfg->maxTravelMmX = 300.0f;
    cfg->maxTravelMmY = 400.0f;
    cfg->maxTravelMmZ = 50.0f;
    sm->handleCommand(cmd("home"), 0);
    sm->tick(StateMachine::kHomingMs);   // -> READY
    doTransfer("{\"version\":1,\"program\":[{\"op\":\"MOVE\",\"x\":100,\"y\":100,\"z\":10},"
               "{\"op\":\"HALT\"}]}");
    sm->handleCommand(cmd("run_program"), 0);
    sm->tick(0);
    TEST_ASSERT_TRUE(State::Idle == sm->state());   // completed, no fault
    TEST_ASSERT_EQUAL(1u, mm->moves.size());
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
    RUN_TEST(test_cal_jog_accumulates_steps);
    RUN_TEST(test_cal_jog_accumulates_signed_net);
    RUN_TEST(test_cal_jog_rejected_outside_calibrating);
    RUN_TEST(test_set_cal_distance_computes_steps_per_mm);
    RUN_TEST(test_set_cal_distance_clears_accumulator);
    RUN_TEST(test_set_cal_distance_rejected_before_jog);
    RUN_TEST(test_set_cal_distance_rejected_outside_calibrating);
    RUN_TEST(test_calibrate_z_axis_independently);
    RUN_TEST(test_calibrate_y_axis_stores_single_value);
    RUN_TEST(test_cancel_calibration_returns_to_idle_without_saving);
    RUN_TEST(test_query_sensors_returns_six_tof_readings);
    RUN_TEST(test_set_max_travel_writes_x);
    RUN_TEST(test_set_max_travel_y_sets_envelope);
    RUN_TEST(test_set_max_travel_rejects_invalid_axis);
    RUN_TEST(test_set_max_travel_rejects_nonpositive);
    RUN_TEST(test_set_max_travel_persists_to_eeprom);
    RUN_TEST(test_set_max_travel_rejected_while_running);
    RUN_TEST(test_program_move_outside_envelope_faults);
    RUN_TEST(test_program_move_inside_envelope_runs);
    RUN_TEST(test_cal_jog_fault_enters_faulted_state);

    // calibrate_sensors + get_param
    RUN_TEST(test_calibrate_sensors_stores_readings_to_config);
    RUN_TEST(test_calibrate_sensors_reads_all_four_channels);
    RUN_TEST(test_calibrate_sensors_saves_to_eeprom);
    RUN_TEST(test_calibrate_sensors_rejected_in_running);
    RUN_TEST(test_get_param_steps_per_mm_x);
    RUN_TEST(test_get_param_steps_per_mm_y1);
    RUN_TEST(test_get_param_steps_per_mm_y2);
    RUN_TEST(test_get_param_tof_offset_0);
    RUN_TEST(test_get_param_tof_offset_3);
    RUN_TEST(test_get_param_uncalibrated_returns_negative);
    RUN_TEST(test_get_param_unknown_key_has_no_value);
    RUN_TEST(test_set_output_pump_reflects_in_status);
    RUN_TEST(test_set_servo_door_open_reflects_in_status);
    RUN_TEST(test_set_servo_laser_press_reflects_in_status);
    RUN_TEST(test_set_servo_unknown_nacks);
    RUN_TEST(test_get_param_echoes_actual_key);
    RUN_TEST(test_get_param_steps_per_mm_y);
    RUN_TEST(test_get_param_max_travel_x);
    RUN_TEST(test_get_param_max_travel_y_and_z);
    return UNITY_END();
}