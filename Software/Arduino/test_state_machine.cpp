// Host-based Unity tests for the StateMachine, run with g++ (see Makefile).
// These are the C++ counterparts of the simulator state-machine tests in
// Software/tests/test_simulator.py — the same behaviours, asserted the same
// way, against MockMachine instead of FakeMachine. Port the rest of the 189
// Python tests into files like this as each layer is implemented.

#include "unity.h"
#include "core/StateMachine.h"
#include "MockMachine.h"

using namespace pnp;

static MockMachine*  mm;
static StateMachine* sm;

void setUp(void)    { mm = new MockMachine(); sm = new StateMachine(*mm); }
void tearDown(void) { delete sm; delete mm; }

static Command cmd(const char* name, int32_t id = 1) {
  Command c;
  c.name = name;
  c.id   = id;
  return c;
}

// ---- command gating ----

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

// ---- homing ----

void test_home_enters_homing_and_requests_home_on_machine(void) {
  Response r = sm->handleCommand(cmd("home"), 0);
  TEST_ASSERT_EQUAL(Response::Ack, r.kind);
  TEST_ASSERT_TRUE(State::Homing == sm->state());
  TEST_ASSERT_EQUAL(1u, mm->homes.size());          // machine.home() requested
  TEST_ASSERT_EQUAL_STRING("XYZ", mm->homes[0].c_str());
}

void test_homing_completes_after_deadline(void) {
  sm->handleCommand(cmd("home"), 0);
  sm->tick(StateMachine::kHomingMs - 1);
  TEST_ASSERT_TRUE(State::Homing == sm->state());
  sm->tick(StateMachine::kHomingMs);
  TEST_ASSERT_TRUE(State::Ready == sm->state());
}

// ---- run requires a loaded program ----

void test_run_program_requires_loaded_program(void) {
  sm->handleCommand(cmd("home"), 0);
  sm->tick(StateMachine::kHomingMs);                 // -> READY

  Response r = sm->handleCommand(cmd("run_program"), 0);
  TEST_ASSERT_EQUAL(Response::Nack, r.kind);
  TEST_ASSERT_EQUAL_STRING("no_program", r.reason);

  sm->setProgramLoaded(true);
  r = sm->handleCommand(cmd("run_program"), 0);
  TEST_ASSERT_EQUAL(Response::Ack, r.kind);
  TEST_ASSERT_TRUE(State::Running == sm->state());
}

// ---- E-stop dominates and releases to IDLE ----

void test_estop_dominates_from_any_state(void) {
  sm->handleCommand(cmd("home"), 0);                 // HOMING
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
  Response r = sm->handleCommand(cmd("reset_estop"), 0);   // latch still active
  TEST_ASSERT_EQUAL(Response::Nack, r.kind);
  TEST_ASSERT_EQUAL_STRING("hw_fault", r.reason);
}

// ---- physical buttons (no ack) ----

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

// ---- safety fault injection ----

void test_injected_jam_faults_with_reason(void) {
  sm->injectFault("motion_fault");
  TEST_ASSERT_TRUE(State::Faulted == sm->state());
  TEST_ASSERT_EQUAL_STRING("motion_fault", sm->fault());
}

// ---- status snapshot ----

void test_status_reflects_state_and_program(void) {
  sm->setProgramLoaded(true);
  StatusSnapshot s = sm->buildStatus();
  TEST_ASSERT_TRUE(State::Idle == s.state);
  TEST_ASSERT_TRUE(s.programLoaded);
  TEST_ASSERT_FALSE(s.estopHw);
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
  return UNITY_END();
}
