// Host Unity tests for the Interpreter — direct translation of the 71 tests
// in Software/tests/test_interpreter.py. Same test names, same assertions,
// against MockMachine instead of FakeMachine.
//
// Test helper: make_interp(jsonStr) parses a JSON program string, loads it
// into an Interpreter backed by a MockMachine, and returns it ready to run.
// Mirrors the make_interp fixture in conftest.py.

#include "unity.h"
#include "core/Interpreter.h"
#include "MockMachine.h"
#include <string.h>
#include <stdio.h>

using namespace pnp;

// ---- per-test state ----
static MockMachine*  mm;
static AbortFlags    flags;
static Interpreter*  interp;
static JsonDocument  doc;    // owns the parsed JSON for the current test

void setUp(void) {
    mm     = new MockMachine();
    interp = new Interpreter(*mm, flags);
    flags.stop  = false;
    flags.pause = false;
}
void tearDown(void) { delete interp; delete mm; }

// ---- helpers ----
static void load(const char* json) {
    doc.clear();
    deserializeJson(doc, json);
    interp->load(doc.as<JsonObjectConst>());
}

// Build a minimal program JSON with a body of ops. Quick helpers for common
// patterns mirror Python's make_interp / _minimal fixtures.
static void prog(const char* bodyJson) {
    char buf[1024];
    snprintf(buf, sizeof(buf),
             "{\"version\":1,\"program\":%s}", bodyJson);
    load(buf);
}

static void progWithConfig(const char* bodyJson, const char* configJson) {
    char buf[1024];
    snprintf(buf, sizeof(buf),
             "{\"version\":1,\"config\":%s,\"program\":%s}", configJson, bodyJson);
    load(buf);
}

static void progWithSubs(const char* bodyJson, const char* subsJson) {
    char buf[1024];
    snprintf(buf, sizeof(buf),
             "{\"version\":1,\"program\":%s,\"subroutines\":%s}", bodyJson, subsJson);
    load(buf);
}

// ===========================================================================
// ProgramValidator
// ===========================================================================

#include "core/ProgramValidator.h"

// Validator instances are created inline per test (Unity has no per-class
// setUp/tearDown, so we don't keep a global instance).

void test_minimal_valid_program_passes(void) {
    ProgramValidator v;
    char err[64] = {};
    bool ok = v.validate(
        "{\"version\":1,\"program\":[{\"op\":\"HALT\"}]}", err, sizeof(err));
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_EQUAL_STRING("", err);
}

void test_non_dict_program_rejected(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate("[1,2,3]", err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "object") != nullptr);
}

void test_missing_version_rejected(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate(
        "{\"program\":[{\"op\":\"HALT\"}]}", err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "version") != nullptr);
}

void test_wrong_version_rejected(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate(
        "{\"version\":2,\"program\":[{\"op\":\"HALT\"}]}", err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "version") != nullptr);
}

void test_missing_program_array_rejected(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate("{\"version\":1}", err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "program") != nullptr);
}

void test_instruction_must_be_object(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate(
        "{\"version\":1,\"program\":[\"HALT\"]}", err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "object") != nullptr);
}

void test_missing_op_rejected(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate(
        "{\"version\":1,\"program\":[{\"x\":1}]}", err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "op") != nullptr);
}

void test_unknown_op_rejected(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate(
        "{\"version\":1,\"program\":[{\"op\":\"TELEPORT\"}]}", err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "unknown op") != nullptr);
}

void test_missing_required_field_rejected(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate(
        "{\"version\":1,\"program\":[{\"op\":\"MOVE\",\"x\":1,\"y\":2}]}",
        err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "'z'") != nullptr);
}

void test_probe_z_approach_z_is_optional(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_TRUE(v.validate(
        "{\"version\":1,\"program\":[{\"op\":\"PROBE_Z\",\"x\":1,\"y\":2,\"store\":\"s\"}]}",
        err, sizeof(err)));
}

void test_call_to_unknown_subroutine_rejected(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate(
        "{\"version\":1,\"program\":[{\"op\":\"CALL\",\"sub\":\"nope\"}]}",
        err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "unknown subroutine") != nullptr);
}

void test_call_to_known_subroutine_passes(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_TRUE(v.validate(
        "{\"version\":1,\"program\":[{\"op\":\"CALL\",\"sub\":\"pick\"}],"
        "\"subroutines\":{\"pick\":[{\"op\":\"RETURN\"}]}}",
        err, sizeof(err)));
}

void test_nested_body_is_validated(void) {
    ProgramValidator v; char err[128] = {};   // 128: nested prefix makes msg >64
    TEST_ASSERT_FALSE(v.validate(
        "{\"version\":1,\"program\":[{\"op\":\"IF\",\"condition\":\"true\","
        "\"then\":[{\"op\":\"MOVE\",\"x\":1,\"y\":2}]}]}",
        err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "'z'") != nullptr);
}

void test_subroutine_bodies_are_validated(void) {
    ProgramValidator v; char err[64] = {};
    TEST_ASSERT_FALSE(v.validate(
        "{\"version\":1,\"program\":[{\"op\":\"CALL\",\"sub\":\"bad\"}],"
        "\"subroutines\":{\"bad\":[{\"op\":\"WAT\"}]}}",
        err, sizeof(err)));
    TEST_ASSERT_TRUE(strstr(err, "unknown op") != nullptr);
}

// ===========================================================================
// Variables
// ===========================================================================

void test_set_var_literal_preserves_value(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"n\",\"value\":5}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    float v; TEST_ASSERT_TRUE(interp->getVar("n", v));
    TEST_ASSERT_EQUAL_FLOAT(5.0f, v);
}

void test_set_var_expr_evaluates_to_float(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"x\",\"value\":5},"
         "{\"op\":\"SET_VAR\",\"name\":\"y\",\"expr\":\"$x * 2\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    float v; interp->getVar("y", v);
    TEST_ASSERT_EQUAL_FLOAT(10.0f, v);
}

void test_dollar_var_resolves_in_move(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"tx\",\"value\":12.0},"
         "{\"op\":\"MOVE\",\"x\":\"$tx\",\"y\":0.0,\"z\":0.0}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_FLOAT(12.0f, mm->moves.back().x);
}

void test_undefined_variable_faults(void) {
    prog("[{\"op\":\"MOVE\",\"x\":\"$missing\",\"y\":0,\"z\":0}]");
    OpResult r = interp->run();
    TEST_ASSERT_EQUAL(OpResult::Faulted, r);
    TEST_ASSERT_TRUE(strstr(interp->faultReason(), "undefined variable") != nullptr);
}

void test_log_expands_variables(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"count\",\"value\":7},"
         "{\"op\":\"LOG\",\"message\":\"did $count items\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_STRING("did 7 items", mm->logs.back().c_str());
}

void test_log_renders_whole_floats_as_ints(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"n\",\"value\":0},"
         "{\"op\":\"SET_VAR\",\"name\":\"n\",\"expr\":\"$n + 1\"},"
         "{\"op\":\"LOG\",\"message\":\"sheet $n done\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_STRING("sheet 1 done", mm->logs.back().c_str());
}

void test_unsafe_expression_faults(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"x\",\"expr\":\"1+abc\"}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_TRUE(strstr(interp->faultReason(), "unsafe expression") != nullptr);
}

// ===========================================================================
// Motion
// ===========================================================================

void test_move_uses_config_default_speed(void) {
    progWithConfig("[{\"op\":\"MOVE\",\"x\":1,\"y\":2,\"z\":3}]",
                   "{\"default_speed_pct\":55}");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL(55, mm->moves.back().speed);
}

void test_move_speed_override(void) {
    prog("[{\"op\":\"MOVE\",\"x\":1,\"y\":2,\"z\":3,\"speed\":30}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL(30, mm->moves.back().speed);
}

void test_move_default_speed_is_80_without_config(void) {
    prog("[{\"op\":\"MOVE\",\"x\":0,\"y\":0,\"z\":0}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL(80, mm->moves.back().speed);
}

void test_move_visits_waypoints_in_order(void) {
    prog("[{\"op\":\"MOVE\",\"x\":10,\"y\":10,\"z\":10,"
         "\"via\":[{\"x\":1,\"y\":1,\"z\":1},{\"x\":5,\"y\":5,\"z\":5}]}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL(3u, mm->moves.size());
    TEST_ASSERT_EQUAL_FLOAT(1.0f, mm->moves[0].x);
    TEST_ASSERT_EQUAL_FLOAT(5.0f, mm->moves[1].x);
    TEST_ASSERT_EQUAL_FLOAT(10.0f, mm->moves[2].x);
}

void test_home_passes_axes_through(void) {
    prog("[{\"op\":\"HOME\",\"axes\":[\"X\",\"Y\",\"Z\"]}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_STRING("XYZ", mm->homes.back().c_str());
}

// ProbeZ
void test_probe_stores_result(void) {
    mm->probeResult = 12.5f;
    prog("[{\"op\":\"PROBE_Z\",\"x\":1,\"y\":2,\"approach_z\":60.0,\"store\":\"surf\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    float v; interp->getVar("surf", v);
    TEST_ASSERT_EQUAL_FLOAT(12.5f, v);
}

void test_probe_uses_explicit_approach_z(void) {
    prog("[{\"op\":\"PROBE_Z\",\"x\":1,\"y\":2,\"approach_z\":60.0,\"store\":\"s\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_FLOAT(60.0f, mm->probes.back().approachZ);
}

void test_probe_defaults_approach_z_to_current_z(void) {
    mm->position = {0.0f, 0.0f, 42.0f};
    prog("[{\"op\":\"PROBE_Z\",\"x\":1,\"y\":2,\"store\":\"s\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_FLOAT(42.0f, mm->probes.back().approachZ);
}

void test_probe_passes_config_tuning(void) {
    progWithConfig(
        "[{\"op\":\"PROBE_Z\",\"x\":1,\"y\":2,\"approach_z\":60,\"store\":\"s\"}]",
        "{\"probe_step_mm\":0.25,\"probe_max_depth_mm\":100.0,\"probe_threshold_mm\":3.0}");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_FLOAT(0.25f,  mm->probes.back().step);
    TEST_ASSERT_EQUAL_FLOAT(100.0f, mm->probes.back().maxDepth);
    TEST_ASSERT_EQUAL_FLOAT(3.0f,   mm->probes.back().threshold);
}

// ===========================================================================
// I/O
// ===========================================================================

void test_output_passthrough(void) {
    prog("[{\"op\":\"OUTPUT\",\"name\":\"pump\",\"value\":true}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_STRING("pump", mm->outputs.back().name.c_str());
    TEST_ASSERT_TRUE(mm->outputs.back().value);
}

void test_read_sensor_stores_value(void) {
    mm->sensors["pickup_ok"] = true;
    prog("[{\"op\":\"READ_SENSOR\",\"sensor\":\"pickup_ok\",\"store\":\"ok\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    float v; interp->getVar("ok", v);
    TEST_ASSERT_EQUAL_FLOAT(1.0f, v);
}

void test_delay_passes_milliseconds(void) {
    prog("[{\"op\":\"DELAY\",\"ms\":250}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL(250u, mm->delays.back());
}

// ===========================================================================
// Conditions
// ===========================================================================

// Helper: run IF(condition) -> LOG "yes"; return true iff "yes" was logged.
static bool evalCond(const char* condition, const char* sensorSetup = nullptr) {
    char buf[512];
    if (sensorSetup) {
        // pre-set sensor via READ_SENSOR trick: just set mm->sensors directly
    }
    snprintf(buf, sizeof(buf),
        "[{\"op\":\"IF\",\"condition\":\"%s\","
        "\"then\":[{\"op\":\"LOG\",\"message\":\"yes\"}]}]", condition);
    prog(buf);
    interp->run();
    return !mm->logs.empty() && mm->logs.back() == "yes";
}

void test_literal_true(void)  { TEST_ASSERT_TRUE(evalCond("true")); }
void test_literal_false(void) { TEST_ASSERT_FALSE(evalCond("false")); }
void test_not_operator(void)  { TEST_ASSERT_TRUE(evalCond("not false")); }

void test_sensor_condition_true(void) {
    mm->sensors["material_present"] = true;
    TEST_ASSERT_TRUE(evalCond("material_present"));
}
void test_sensor_condition_false(void) {
    mm->sensors["material_present"] = false;
    TEST_ASSERT_FALSE(evalCond("material_present"));
}
void test_not_sensor(void) {
    mm->sensors["material_present"] = false;
    TEST_ASSERT_TRUE(evalCond("not material_present"));
}

// Numeric comparisons — set $n=3 with SET_VAR then evaluate.
static bool evalNumCond(const char* cond) {
    char buf[256];
    snprintf(buf, sizeof(buf),
        "[{\"op\":\"SET_VAR\",\"name\":\"n\",\"value\":3},"
        "{\"op\":\"IF\",\"condition\":\"%s\","
        "\"then\":[{\"op\":\"LOG\",\"message\":\"yes\"}]}]", cond);
    prog(buf);
    interp->run();
    return !mm->logs.empty() && mm->logs.back() == "yes";
}

void test_numeric_eq(void)  { TEST_ASSERT_TRUE(evalNumCond("$n == 3")); }
void test_numeric_neq(void) { TEST_ASSERT_FALSE(evalNumCond("$n != 3")); }
void test_numeric_gte(void) { TEST_ASSERT_TRUE(evalNumCond("$n >= 3")); }
void test_numeric_lte(void) { TEST_ASSERT_TRUE(evalNumCond("$n <= 3")); }
void test_numeric_gt(void)  { TEST_ASSERT_TRUE(evalNumCond("$n > 2")); }
void test_numeric_lt(void)  { TEST_ASSERT_FALSE(evalNumCond("$n < 3")); }

void test_boolean_rhs_comparison(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"flag\",\"value\":true},"
         "{\"op\":\"IF\",\"condition\":\"$flag == true\","
         "\"then\":[{\"op\":\"LOG\",\"message\":\"yes\"}]}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_FALSE(mm->logs.empty());
    TEST_ASSERT_EQUAL_STRING("yes", mm->logs.back().c_str());
}

void test_comparison_on_undefined_var_faults(void) {
    prog("[{\"op\":\"IF\",\"condition\":\"$ghost > 1\","
         "\"then\":[{\"op\":\"HALT\"}]}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_TRUE(strstr(interp->faultReason(), "undefined variable") != nullptr);
}

void test_invalid_condition_faults(void) {
    prog("[{\"op\":\"IF\",\"condition\":\"wibble wobble\","
         "\"then\":[{\"op\":\"HALT\"}]}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_TRUE(strstr(interp->faultReason(), "invalid condition") != nullptr);
}

// ===========================================================================
// Flow control
// ===========================================================================

void test_if_then_branch(void) {
    prog("[{\"op\":\"IF\",\"condition\":\"true\","
         "\"then\":[{\"op\":\"LOG\",\"message\":\"T\"}],"
         "\"else\":[{\"op\":\"LOG\",\"message\":\"F\"}]}]");
    interp->run();
    TEST_ASSERT_EQUAL_STRING("T", mm->logs.back().c_str());
}

void test_if_else_branch(void) {
    prog("[{\"op\":\"IF\",\"condition\":\"false\","
         "\"then\":[{\"op\":\"LOG\",\"message\":\"T\"}],"
         "\"else\":[{\"op\":\"LOG\",\"message\":\"F\"}]}]");
    interp->run();
    TEST_ASSERT_EQUAL_STRING("F", mm->logs.back().c_str());
}

void test_if_without_else_is_noop_when_false(void) {
    prog("[{\"op\":\"IF\",\"condition\":\"false\","
         "\"then\":[{\"op\":\"LOG\",\"message\":\"T\"}]}]");
    interp->run();
    TEST_ASSERT_TRUE(mm->logs.empty());
}

void test_loop_for_runs_count_times(void) {
    prog("[{\"op\":\"LOOP_FOR\",\"count\":3,"
         "\"body\":[{\"op\":\"LOG\",\"message\":\"tick\"}]}]");
    interp->run();
    TEST_ASSERT_EQUAL(3u, mm->logs.size());
}

void test_loop_for_exposes_loop_index(void) {
    prog("[{\"op\":\"LOOP_FOR\",\"count\":2,"
         "\"body\":[{\"op\":\"LOG\",\"message\":\"i=$_loop_i\"}]}]");
    interp->run();
    TEST_ASSERT_EQUAL_STRING("i=0", mm->logs[0].c_str());
    TEST_ASSERT_EQUAL_STRING("i=1", mm->logs[1].c_str());
}

void test_loop_while_terminates_on_condition(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"n\",\"value\":0},"
         "{\"op\":\"LOOP_WHILE\",\"condition\":\"$n < 3\",\"body\":["
         "{\"op\":\"LOG\",\"message\":\"x\"},"
         "{\"op\":\"SET_VAR\",\"name\":\"n\",\"expr\":\"$n + 1\"}]}]");
    interp->run();
    TEST_ASSERT_EQUAL(3u, mm->logs.size());
}

void test_loop_while_overflow_faults(void) {
    prog("[{\"op\":\"LOOP_WHILE\",\"condition\":\"true\",\"body\":[]}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_TRUE(strstr(interp->faultReason(), "loop_overflow") != nullptr);
}

void test_halt_stops_execution(void) {
    prog("[{\"op\":\"LOG\",\"message\":\"before\"},"
         "{\"op\":\"HALT\"},"
         "{\"op\":\"LOG\",\"message\":\"after\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL(1u, mm->logs.size());
    TEST_ASSERT_EQUAL_STRING("before", mm->logs[0].c_str());
}

void test_fault_raises_with_expanded_reason(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"code\",\"value\":7},"
         "{\"op\":\"FAULT\",\"reason\":\"bad_$code\"}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_EQUAL_STRING("bad_7", interp->faultReason());
}

void test_jump_is_unsupported_in_v1(void) {
    prog("[{\"op\":\"JUMP\",\"to\":\"label\"}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_TRUE(strstr(interp->faultReason(), "JUMP is not supported") != nullptr);
}

// ===========================================================================
// Subroutines
// ===========================================================================

void test_call_executes_subroutine(void) {
    progWithSubs("[{\"op\":\"CALL\",\"sub\":\"greet\"}]",
                 "{\"greet\":[{\"op\":\"LOG\",\"message\":\"hi\"}]}");
    interp->run();
    TEST_ASSERT_EQUAL_STRING("hi", mm->logs.back().c_str());
}

void test_return_exits_subroutine_early(void) {
    progWithSubs(
        "[{\"op\":\"CALL\",\"sub\":\"s\"},{\"op\":\"LOG\",\"message\":\"after\"}]",
        "{\"s\":["
        "{\"op\":\"LOG\",\"message\":\"in\"},"
        "{\"op\":\"RETURN\"},"
        "{\"op\":\"LOG\",\"message\":\"unreached\"}]}");
    interp->run();
    TEST_ASSERT_EQUAL(2u, mm->logs.size());
    TEST_ASSERT_EQUAL_STRING("in",    mm->logs[0].c_str());
    TEST_ASSERT_EQUAL_STRING("after", mm->logs[1].c_str());
}

void test_call_unknown_subroutine_faults(void) {
    prog("[{\"op\":\"CALL\",\"sub\":\"ghost\"}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_TRUE(strstr(interp->faultReason(), "unknown subroutine") != nullptr);
}

void test_call_depth_limit_faults(void) {
    progWithSubs("[{\"op\":\"CALL\",\"sub\":\"rec\"}]",
                 "{\"rec\":[{\"op\":\"CALL\",\"sub\":\"rec\"}]}");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_TRUE(strstr(interp->faultReason(), "call_depth") != nullptr);
}

// ===========================================================================
// WAIT
// ===========================================================================

void test_wait_returns_immediately_when_satisfied(void) {
    mm->sensors["laser_safe"] = true;
    prog("[{\"op\":\"WAIT\",\"condition\":\"laser_safe\",\"timeout_ms\":1000}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
}

void test_wait_times_out_with_custom_fault(void) {
    mm->sensors["laser_safe"] = false;
    prog("[{\"op\":\"WAIT\",\"condition\":\"laser_safe\","
         "\"timeout_ms\":10,\"timeout_fault\":\"laser_interlock\"}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_EQUAL_STRING("laser_interlock", interp->faultReason());
}

void test_wait_satisfied_after_sensor_flips(void) {
    mm->sensors["laser_safe"] = false;
    mm->sensorFlipAfter = 1;  // flip to true after 1 read
    prog("[{\"op\":\"WAIT\",\"condition\":\"laser_safe\",\"timeout_ms\":5000}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_TRUE(mm->sensorReadCount >= 2);
}

// ===========================================================================
// Pause / Stop
// ===========================================================================

void test_stop_event_aborts_before_next_instruction(void) {
    flags.stop = true;
    prog("[{\"op\":\"LOG\",\"message\":\"never\"}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_EQUAL_STRING("estop_triggered", interp->faultReason());
    TEST_ASSERT_TRUE(mm->logs.empty());
}

void test_stop_during_pause_aborts(void) {
    flags.pause = true;
    flags.stop  = true;
    prog("[{\"op\":\"LOG\",\"message\":\"x\"}]");
    TEST_ASSERT_EQUAL(OpResult::Faulted, interp->run());
    TEST_ASSERT_EQUAL_STRING("estop_triggered", interp->faultReason());
}

void test_runs_to_completion_when_not_stopped(void) {
    prog("[{\"op\":\"LOG\",\"message\":\"done\"},{\"op\":\"HALT\"}]");
    TEST_ASSERT_EQUAL(OpResult::Ok, interp->run());
    TEST_ASSERT_EQUAL_STRING("done", mm->logs.back().c_str());
}

// ===========================================================================
// Status
// ===========================================================================

void test_status_reports_step_index_and_vars(void) {
    prog("[{\"op\":\"SET_VAR\",\"name\":\"a\",\"value\":1}]");
    interp->run();
    TEST_ASSERT_TRUE(interp->status().stepIndex >= 1);
    float v; interp->getVar("a", v);
    TEST_ASSERT_EQUAL_FLOAT(1.0f, v);
}

// ===========================================================================
// main
// ===========================================================================

int main(void) {
    UNITY_BEGIN();

    // Validator
    RUN_TEST(test_minimal_valid_program_passes);
    RUN_TEST(test_non_dict_program_rejected);
    RUN_TEST(test_missing_version_rejected);
    RUN_TEST(test_wrong_version_rejected);
    RUN_TEST(test_missing_program_array_rejected);
    RUN_TEST(test_instruction_must_be_object);
    RUN_TEST(test_missing_op_rejected);
    RUN_TEST(test_unknown_op_rejected);
    RUN_TEST(test_missing_required_field_rejected);
    RUN_TEST(test_probe_z_approach_z_is_optional);
    RUN_TEST(test_call_to_unknown_subroutine_rejected);
    RUN_TEST(test_call_to_known_subroutine_passes);
    RUN_TEST(test_nested_body_is_validated);
    RUN_TEST(test_subroutine_bodies_are_validated);

    // Variables
    RUN_TEST(test_set_var_literal_preserves_value);
    RUN_TEST(test_set_var_expr_evaluates_to_float);
    RUN_TEST(test_dollar_var_resolves_in_move);
    RUN_TEST(test_undefined_variable_faults);
    RUN_TEST(test_log_expands_variables);
    RUN_TEST(test_log_renders_whole_floats_as_ints);
    RUN_TEST(test_unsafe_expression_faults);

    // Motion
    RUN_TEST(test_move_uses_config_default_speed);
    RUN_TEST(test_move_speed_override);
    RUN_TEST(test_move_default_speed_is_80_without_config);
    RUN_TEST(test_move_visits_waypoints_in_order);
    RUN_TEST(test_home_passes_axes_through);
    RUN_TEST(test_probe_stores_result);
    RUN_TEST(test_probe_uses_explicit_approach_z);
    RUN_TEST(test_probe_defaults_approach_z_to_current_z);
    RUN_TEST(test_probe_passes_config_tuning);

    // I/O
    RUN_TEST(test_output_passthrough);
    RUN_TEST(test_read_sensor_stores_value);
    RUN_TEST(test_delay_passes_milliseconds);

    // Conditions
    RUN_TEST(test_literal_true);
    RUN_TEST(test_literal_false);
    RUN_TEST(test_not_operator);
    RUN_TEST(test_sensor_condition_true);
    RUN_TEST(test_sensor_condition_false);
    RUN_TEST(test_not_sensor);
    RUN_TEST(test_numeric_eq);
    RUN_TEST(test_numeric_neq);
    RUN_TEST(test_numeric_gte);
    RUN_TEST(test_numeric_lte);
    RUN_TEST(test_numeric_gt);
    RUN_TEST(test_numeric_lt);
    RUN_TEST(test_boolean_rhs_comparison);
    RUN_TEST(test_comparison_on_undefined_var_faults);
    RUN_TEST(test_invalid_condition_faults);

    // Flow control
    RUN_TEST(test_if_then_branch);
    RUN_TEST(test_if_else_branch);
    RUN_TEST(test_if_without_else_is_noop_when_false);
    RUN_TEST(test_loop_for_runs_count_times);
    RUN_TEST(test_loop_for_exposes_loop_index);
    RUN_TEST(test_loop_while_terminates_on_condition);
    RUN_TEST(test_loop_while_overflow_faults);
    RUN_TEST(test_halt_stops_execution);
    RUN_TEST(test_fault_raises_with_expanded_reason);
    RUN_TEST(test_jump_is_unsupported_in_v1);

    // Subroutines
    RUN_TEST(test_call_executes_subroutine);
    RUN_TEST(test_return_exits_subroutine_early);
    RUN_TEST(test_call_unknown_subroutine_faults);
    RUN_TEST(test_call_depth_limit_faults);

    // WAIT
    RUN_TEST(test_wait_returns_immediately_when_satisfied);
    RUN_TEST(test_wait_times_out_with_custom_fault);
    RUN_TEST(test_wait_satisfied_after_sensor_flips);

    // Pause/Stop
    RUN_TEST(test_stop_event_aborts_before_next_instruction);
    RUN_TEST(test_stop_during_pause_aborts);
    RUN_TEST(test_runs_to_completion_when_not_stopped);

    // Status
    RUN_TEST(test_status_reports_step_index_and_vars);

    return UNITY_END();
}
