"""
Unit tests for simulator.py — StateMachine, MachineState, SimulatedMachine.

These cover the non-GUI half of the system: the command/gating layer the GUI
talks to over TCP, the chunked-transfer protocol, state transitions, the
derived sensor properties, and the SimulatedMachine that backs the interpreter.

The TCP server and console loop are intentionally NOT tested here — they're
thin glue around StateMachine, which is exercised directly via enqueue + tick.

Run from the Software/ directory:  pytest -q
"""

import base64
import itertools
import json
import queue
import threading

import pytest

import simulator
from simulator import MachineState, SimulatedMachine, State, StateMachine
from interpreter import ProgramFault


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    """
    Keep tests off the filesystem and deterministic:
      * _save_positions (called by teach/save) must not write pnp_positions.json
      * _load_positions must return {} so MachineState's built-in defaults apply
        regardless of what's in the working directory.
    """
    monkeypatch.setattr(simulator, "_save_positions", lambda *a, **k: None)
    monkeypatch.setattr(simulator, "_load_positions", lambda: {})


@pytest.fixture
def sm(_no_disk):
    """A fresh StateMachine in the default IDLE state."""
    return StateMachine()


def drain(sm):
    """Pop and JSON-parse every queued outgoing message."""
    out = []
    while not sm.out_queue.empty():
        out.append(json.loads(sm.out_queue.get_nowait()))
    return out


def step(sm, msg):
    """Enqueue one command, tick once, return the parsed outgoing messages."""
    sm.enqueue_command(msg)
    sm.tick()
    return drain(sm)


def only(msgs, cmd=None):
    """Return the single relevant response (last message, optionally for cmd)."""
    if cmd is not None:
        msgs = [m for m in msgs if m.get("cmd") == cmd]
    assert msgs, "expected at least one matching response"
    return msgs[-1]


# ===========================================================================
# MachineState — derived sensor properties
# ===========================================================================

class TestMachineStateProperties:

    def test_pickup_ok_when_all_corners_close_and_valid(self):
        ms = MachineState()
        ms.tof_dist_mm = [100, 100, 100, 100, 30, 25]
        ms.tof_valid = [True] * 6
        assert ms.pickup_ok is True

    def test_pickup_not_ok_if_a_corner_is_out_of_range(self):
        ms = MachineState()
        ms.tof_dist_mm = [100, 100, 250, 100, 30, 25]   # ch2 >= 200
        assert ms.pickup_ok is False

    def test_pickup_not_ok_if_a_corner_is_invalid(self):
        ms = MachineState()
        ms.tof_dist_mm = [100, 100, 100, 100, 30, 25]
        ms.tof_valid = [True, True, False, True, True, True]
        assert ms.pickup_ok is False

    def test_material_present_when_ch5_close_and_valid(self):
        ms = MachineState()
        ms.tof_dist_mm[5] = 25
        ms.tof_valid[5] = True
        assert ms.material_present is True

    def test_material_absent_when_ch5_far(self):
        ms = MachineState()
        ms.tof_dist_mm[5] = 300
        assert ms.material_present is False

    def test_material_absent_when_ch5_invalid(self):
        ms = MachineState()
        ms.tof_dist_mm[5] = 25
        ms.tof_valid[5] = False
        assert ms.material_present is False

    def test_laser_safe_tracks_head_home(self):
        ms = MachineState()
        ms.laser_head_home = True
        assert ms.laser_safe is True
        ms.laser_head_home = False
        assert ms.laser_safe is False

    def test_resolve_position_name_matches_within_tolerance(self):
        ms = MachineState()
        ms.taught["home"] = (10.0, 20.0, 5.0)
        ms.x_mm, ms.y_mm = 10.5, 20.5     # within 1mm in X and Y
        assert ms.resolve_position_name() == "home"

    def test_resolve_position_name_none_when_off_grid(self):
        ms = MachineState()
        ms.x_mm, ms.y_mm = 999.0, 999.0
        assert ms.resolve_position_name() is None


# ===========================================================================
# StateMachine — command gating
# ===========================================================================

class TestCommandGating:

    def test_missing_id_is_nacked(self, sm):
        resp = only(step(sm, {"cmd": "query_status"}))
        assert resp["type"] == "nack" and resp["reason"] == "missing_id"

    def test_missing_cmd_is_nacked(self, sm):
        resp = only(step(sm, {"id": 1}))
        assert resp["type"] == "nack" and resp["reason"] == "malformed"

    def test_unknown_command_is_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "do_a_barrel_roll"}))
        assert resp["type"] == "nack" and resp["reason"] == "unknown_cmd"

    def test_command_rejected_in_wrong_state_not_ready(self, sm):
        # run_program is only valid in READY; IDLE -> not_ready
        resp = only(step(sm, {"id": 1, "cmd": "run_program"}))
        assert resp["type"] == "nack" and resp["reason"] == "not_ready"

    def test_command_rejected_in_faulted_state(self, sm):
        sm.ms.set_state(State.FAULTED)
        resp = only(step(sm, {"id": 1, "cmd": "home"}))
        assert resp["type"] == "nack" and resp["reason"] == "hw_fault"

    def test_command_rejected_in_estopped_state(self, sm):
        sm.ms.set_state(State.ESTOPPED)
        resp = only(step(sm, {"id": 1, "cmd": "home"}))
        assert resp["type"] == "nack" and resp["reason"] == "estop_active"

    def test_always_accept_command_works_in_any_state(self, sm):
        sm.ms.set_state(State.ESTOPPED)
        resp = only(step(sm, {"id": 1, "cmd": "query_status"}))
        assert resp["type"] == "status"

    def test_valid_command_in_allowed_state_is_acked(self, sm):
        resp = only(step(sm, {"id": 7, "cmd": "home"}))
        assert resp["type"] == "ack" and resp["id"] == 7


# ===========================================================================
# StateMachine — state transitions
# ===========================================================================

class TestStateTransitions:

    def test_home_enters_homing(self, sm):
        step(sm, {"id": 1, "cmd": "home"})
        assert sm.ms.state == State.HOMING

    def test_homing_completes_to_ready_after_timer(self, sm):
        step(sm, {"id": 1, "cmd": "home"})
        # Pull the completion deadline into the past instead of waiting 3s.
        sm._homing_done_at = simulator.time.monotonic() - 0.01
        sm.tick()
        assert sm.ms.state == State.READY
        assert (sm.ms.x_mm, sm.ms.y_mm, sm.ms.z_mm) == (0.0, 0.0, 0.0)

    def test_load_program_valid_is_acked_and_stored(self, sm):
        prog = {"version": 1, "program": [{"op": "HALT"}]}
        resp = only(step(sm, {"id": 1, "cmd": "load_program", "program": prog}))
        assert resp["type"] == "ack"
        assert resp["instructions"] == 1
        assert sm.ms.stored_program == prog

    def test_load_program_invalid_is_nacked_with_detail(self, sm):
        prog = {"version": 1, "program": [{"op": "MOVE", "x": 1, "y": 2}]}
        resp = only(step(sm, {"id": 1, "cmd": "load_program", "program": prog}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"
        assert "z" in resp["detail"]

    def test_load_program_missing_object_is_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "load_program"}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_run_program_without_program_is_nacked(self, sm):
        sm.ms.set_state(State.READY)
        resp = only(step(sm, {"id": 1, "cmd": "run_program"}))
        assert resp["type"] == "nack" and resp["reason"] == "no_program"

    def test_run_program_starts_and_halts_back_to_ready(self, sm):
        # A short DELAY keeps the interpreter thread busy long enough that the
        # RUNNING state is observable; a bare HALT would finish inside the same
        # tick and the RUNNING->READY transition would be a race.
        sm.ms.stored_program = {"version": 1,
                                "program": [{"op": "DELAY", "ms": 150},
                                            {"op": "HALT"}]}
        sm.ms.set_state(State.READY)
        resp = only(step(sm, {"id": 1, "cmd": "run_program"}))
        assert resp["type"] == "ack"
        assert sm.ms.state == State.RUNNING
        # Let the thread finish; the next tick observes the 'halt' result and
        # returns the machine to READY.
        sm._interp_thread.join(timeout=2.0)
        sm.tick()
        assert sm.ms.state == State.READY

    def test_pause_and_resume(self, sm):
        sm.ms.set_state(State.RUNNING)
        step(sm, {"id": 1, "cmd": "pause"})
        assert sm.ms.state == State.PAUSED
        assert sm._pause_event.is_set()
        step(sm, {"id": 2, "cmd": "resume"})
        assert sm.ms.state == State.RUNNING
        assert not sm._pause_event.is_set()

    def test_estop_enters_estopped_and_sets_stop_event(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "estop"})
        assert sm.ms.state == State.ESTOPPED
        assert sm._stop_event.is_set()
        assert sm.ms.fault == "estop_triggered"
        assert any(m["type"] == "ack" and m["cmd"] == "estop" for m in msgs)

    def test_reset_fault_returns_to_idle(self, sm):
        sm.ms.set_state(State.FAULTED)
        sm.ms.fault = "motion_fault"
        step(sm, {"id": 1, "cmd": "reset_fault"})
        assert sm.ms.state == State.IDLE and sm.ms.fault is None

    def test_reset_estop_returns_to_idle(self, sm):
        sm.ms.set_state(State.ESTOPPED)
        sm._stop_event.set()
        step(sm, {"id": 1, "cmd": "reset_estop"})
        assert sm.ms.state == State.IDLE
        assert not sm._stop_event.is_set()

    def test_reset_estop_blocked_while_hw_estop_held(self, sm):
        sm.ms.set_state(State.ESTOPPED)
        sm.ms.estop_hw = True
        resp = only(step(sm, {"id": 1, "cmd": "reset_estop"}))
        assert resp["type"] == "nack" and resp["reason"] == "hw_fault"
        assert sm.ms.state == State.ESTOPPED


# ===========================================================================
# StateMachine — chunked program transfer protocol
# ===========================================================================

class TestChunkedTransfer:

    VALID = {"version": 1, "program": [{"op": "LOG", "message": "hi"},
                                       {"op": "HALT"}]}

    def _begin(self, sm, size, chunks):
        return only(step(sm, {"id": 1, "cmd": "begin_transfer",
                              "size": size, "chunks": chunks}))

    def _chunk(self, sm, index, raw_bytes):
        data = base64.b64encode(raw_bytes).decode()
        return only(step(sm, {"id": 1, "cmd": "program_chunk",
                              "index": index, "data": data}))

    def test_begin_rejects_zero_size(self, sm):
        resp = self._begin(sm, size=0, chunks=1)
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_begin_rejects_zero_chunks(self, sm):
        resp = self._begin(sm, size=10, chunks=0)
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_begin_resets_buffers_and_acks(self, sm):
        resp = self._begin(sm, size=10, chunks=2)
        assert resp["type"] == "ack"
        assert sm.ms.xfer_size == 10 and sm.ms.xfer_chunks == 2
        assert sm.ms.xfer_received == 0 and sm.ms.xfer_buf == b""

    def test_chunk_without_transfer_is_nacked(self, sm):
        resp = self._chunk(sm, 0, b"abc")
        assert resp["type"] == "nack"
        assert resp["reason"] == "no_transfer_in_progress"

    def test_chunk_out_of_order_is_nacked(self, sm):
        self._begin(sm, size=6, chunks=2)
        resp = self._chunk(sm, 1, b"abc")   # expected index 0
        assert resp["type"] == "nack"
        assert resp["reason"] == "out_of_order_expected_0"

    def test_chunk_bad_base64_is_nacked(self, sm):
        self._begin(sm, size=6, chunks=1)
        resp = only(step(sm, {"id": 1, "cmd": "program_chunk",
                              "index": 0, "data": "!!!not-base64!!!"}))
        assert resp["type"] == "nack" and resp["reason"] == "bad_base64"

    def test_chunk_in_order_is_acked(self, sm):
        self._begin(sm, size=6, chunks=2)
        resp = self._chunk(sm, 0, b"abc")
        assert resp["type"] == "ack" and resp["index"] == 0
        assert sm.ms.xfer_received == 1

    def test_end_without_transfer_is_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "end_transfer"}))
        assert resp["type"] == "nack"
        assert resp["reason"] == "no_transfer_in_progress"

    def test_end_with_missing_chunks_is_nacked(self, sm):
        self._begin(sm, size=6, chunks=2)
        self._chunk(sm, 0, b"abc")          # only 1 of 2
        resp = only(step(sm, {"id": 1, "cmd": "end_transfer"}))
        assert resp["type"] == "nack"
        assert resp["reason"] == "incomplete_1_of_2"

    def test_end_with_size_mismatch_is_nacked(self, sm):
        self._begin(sm, size=99, chunks=1)   # claim 99 bytes
        self._chunk(sm, 0, b"abc")           # send 3
        resp = only(step(sm, {"id": 1, "cmd": "end_transfer"}))
        assert resp["type"] == "nack" and resp["reason"] == "size_mismatch"

    def test_end_with_invalid_json_is_nacked(self, sm):
        payload = b"{not valid json"
        self._begin(sm, size=len(payload), chunks=1)
        self._chunk(sm, 0, payload)
        resp = only(step(sm, {"id": 1, "cmd": "end_transfer"}))
        assert resp["type"] == "nack" and "json_error" in resp["reason"]

    def test_full_transfer_happy_path_loads_program(self, sm):
        payload = json.dumps(self.VALID).encode()
        self._begin(sm, size=len(payload), chunks=1)
        self._chunk(sm, 0, payload)
        resp = only(step(sm, {"id": 1, "cmd": "end_transfer"}))
        # end_transfer delegates to load_program on success.
        assert resp["type"] == "ack" and resp["cmd"] == "load_program"
        assert sm.ms.stored_program == self.VALID

    def test_full_transfer_in_multiple_chunks(self, sm):
        payload = json.dumps(self.VALID).encode()
        mid = len(payload) // 2
        parts = [payload[:mid], payload[mid:]]
        self._begin(sm, size=len(payload), chunks=2)
        self._chunk(sm, 0, parts[0])
        self._chunk(sm, 1, parts[1])
        resp = only(step(sm, {"id": 1, "cmd": "end_transfer"}))
        assert resp["type"] == "ack" and resp["cmd"] == "load_program"
        assert sm.ms.stored_program == self.VALID


# ===========================================================================
# StateMachine — I/O and query commands
# ===========================================================================

class TestIOCommands:

    def test_set_output_pump_on(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "set_output",
                              "output": "pump", "state": True}))
        assert resp["type"] == "ack" and sm.ms.pump is True

    def test_set_output_unknown_name_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "set_output",
                              "output": "frobnicator", "state": True}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_set_output_non_bool_state_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "set_output",
                              "output": "pump", "state": "yes"}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_set_servo_door_open(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "set_servo",
                              "servo": "door", "position": "open"}))
        assert resp["type"] == "ack" and sm.ms.servo_door == "open"

    def test_set_servo_unknown_servo_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "set_servo",
                              "servo": "elbow", "position": "open"}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_set_servo_invalid_position_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "set_servo",
                              "servo": "door", "position": "ajar"}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_jog_moves_axis(self, sm):
        sm.ms.set_state(State.READY)
        x0 = sm.ms.x_mm
        resp = only(step(sm, {"id": 1, "cmd": "jog",
                              "axis": "x", "distance_mm": 5.0, "dir": 1}))
        assert resp["type"] == "ack" and sm.ms.x_mm == x0 + 5.0

    def test_jog_invalid_axis_nacked(self, sm):
        sm.ms.set_state(State.READY)
        resp = only(step(sm, {"id": 1, "cmd": "jog",
                              "axis": "Q", "distance_mm": 5.0, "dir": 1}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_teach_position_stores_current_coords(self, sm):
        sm.ms.set_state(State.READY)
        sm.ms.x_mm, sm.ms.y_mm, sm.ms.z_mm = 11.0, 22.0, 33.0
        resp = only(step(sm, {"id": 1, "cmd": "teach_position",
                              "name": "home"}))
        assert resp["type"] == "ack"
        assert sm.ms.taught["home"] == (11.0, 22.0, 33.0)

    def test_teach_position_invalid_name_nacked(self, sm):
        sm.ms.set_state(State.READY)
        resp = only(step(sm, {"id": 1, "cmd": "teach_position",
                              "name": "nowhere"}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_save_position_stores_given_coords(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "save_position",
                              "name": "deposit",
                              "x_mm": 1.0, "y_mm": 2.0, "z_mm": 3.0}))
        assert resp["type"] == "ack"
        assert sm.ms.taught["deposit"] == (1.0, 2.0, 3.0)

    def test_set_param_known_key(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "set_param",
                              "key": "status_rate_hz", "value": 10}))
        assert resp["type"] == "ack" and sm.ms.params["status_rate_hz"] == 10

    def test_set_param_unknown_key_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "set_param",
                              "key": "warp_factor", "value": 9}))
        assert resp["type"] == "nack" and resp["reason"] == "invalid_param"

    def test_get_param_returns_value(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "get_param",
                              "key": "status_rate_hz"}))
        assert resp["type"] == "ack" and resp["value"] == sm.ms.params[
            "status_rate_hz"]

    def test_query_position_reports_coords(self, sm):
        sm.ms.x_mm, sm.ms.y_mm, sm.ms.z_mm = 1.234, 5.678, 9.0
        resp = only(step(sm, {"id": 1, "cmd": "query_position"}))
        assert resp["type"] == "ack"
        assert resp["x_mm"] == 1.23 and resp["y_mm"] == 5.68

    def test_query_sensors_reports_tof_outputs_inputs(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "query_sensors"}))
        assert resp["type"] == "ack"
        assert len(resp["tof"]) == 6
        assert "pump" in resp["outputs"] and "estop_hw" in resp["inputs"]

    def test_query_positions_reports_all_named(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "query_positions"}))
        assert resp["type"] == "ack"
        assert set(resp["positions"]) >= {"home", "laser_a", "laser_b",
                                          "deposit"}

    def test_get_program_without_program_nacked(self, sm):
        resp = only(step(sm, {"id": 1, "cmd": "get_program"}))
        assert resp["type"] == "nack" and resp["reason"] == "no_program"

    def test_get_program_returns_stored(self, sm):
        prog = {"version": 1, "program": [{"op": "HALT"}]}
        sm.ms.stored_program = prog
        resp = only(step(sm, {"id": 1, "cmd": "get_program"}))
        assert resp["type"] == "ack" and resp["program"] == prog


# ===========================================================================
# StateMachine — internal hardware events
# ===========================================================================

class TestInternalEvents:

    def test_injected_fault_transitions_to_faulted(self, sm):
        sm.enqueue_fault("motion_fault")
        sm.tick()
        msgs = drain(sm)
        assert sm.ms.state == State.FAULTED and sm.ms.fault == "motion_fault"
        assert any(m["type"] == "fault" for m in msgs)

    def test_hw_estop_engaged(self, sm):
        sm.enqueue_estop(released=False)
        sm.tick()
        assert sm.ms.state == State.ESTOPPED
        assert sm.ms.estop_hw is True
        assert sm._stop_event.is_set()

    def test_hw_estop_released_clears_flag(self, sm):
        sm.ms.estop_hw = True
        sm.enqueue_estop(released=True)
        sm.tick()
        assert sm.ms.estop_hw is False

    def test_laser_state_updates_safety_and_tof(self, sm):
        sm.enqueue_laser_state(home=False)
        sm.tick()
        assert sm.ms.laser_head_home is False
        assert sm.ms.laser_safe is False
        assert sm.ms.tof_dist_mm[4] == 400

    def test_material_state_updates_presence(self, sm):
        sm.enqueue_material_state(present=True)
        sm.tick()
        assert sm.ms.material_present is True
        sm.enqueue_material_state(present=False)
        sm.tick()
        assert sm.ms.material_present is False


# ===========================================================================
# Headless physical-button emulation (GUI-attached status propagation)
# ===========================================================================

class TestPhysicalButtons:
    """A physical button drives the same transition as the GUI command, but
    sends NO ack — an attached GUI learns of the change from the next status
    broadcast. These tests assert the transition happens AND no ack is emitted."""

    def _press(self, sm, button):
        sm.enqueue_button(button)
        sm.tick()
        return drain(sm)

    def test_start_in_idle_homes(self, sm):
        msgs = self._press(sm, "start")
        assert sm.ms.state == State.HOMING
        # No command ack/nack — the GUI didn't send anything.
        assert all(m["type"] not in ("ack", "nack") for m in msgs)

    def test_start_in_ready_with_program_runs(self, sm):
        sm.ms.stored_program = {"version": 1,
                                "program": [{"op": "DELAY", "ms": 150},
                                            {"op": "HALT"}]}
        sm.ms.set_state(State.READY)
        msgs = self._press(sm, "start")
        assert sm.ms.state == State.RUNNING
        assert all(m["type"] not in ("ack", "nack") for m in msgs)
        sm._interp_thread.join(timeout=2.0)
        sm.tick()
        assert sm.ms.state == State.READY

    def test_start_in_ready_without_program_is_refused(self, sm):
        sm.ms.set_state(State.READY)
        self._press(sm, "start")
        assert sm.ms.state == State.READY   # no program -> no transition

    def test_start_in_paused_resumes(self, sm):
        sm.ms.set_state(State.PAUSED)
        sm._pause_event.set()
        self._press(sm, "start")
        assert sm.ms.state == State.RUNNING
        assert not sm._pause_event.is_set()

    def test_pause_in_running_pauses(self, sm):
        sm.ms.set_state(State.RUNNING)
        self._press(sm, "pause")
        assert sm.ms.state == State.PAUSED
        assert sm._pause_event.is_set()

    def test_pause_in_faulted_clears_fault(self, sm):
        sm.ms.set_state(State.FAULTED)
        sm.ms.fault = "motion_fault"
        sm._stop_event.set()
        self._press(sm, "pause")
        assert sm.ms.state == State.IDLE
        assert sm.ms.fault is None
        assert not sm._stop_event.is_set()

    def test_button_is_noop_in_inapplicable_state(self, sm):
        sm.ms.set_state(State.HOMING)
        self._press(sm, "start")
        self._press(sm, "pause")
        assert sm.ms.state == State.HOMING

    def test_button_change_appears_in_next_status_broadcast(self, sm):
        # The mechanism the GUI relies on: state set by a button shows up in
        # the broadcast status payload.
        self._press(sm, "start")
        status = sm._build_status()
        assert status["state"] == "HOMING"


# ===========================================================================
# Fault / error injection for GUI testing
# ===========================================================================

class TestFaultInjection:

    def test_jam_raises_motion_fault_with_axis(self, sm):
        sm.enqueue_jam("Y2")
        sm.tick()
        msgs = drain(sm)
        assert sm.ms.state == State.FAULTED
        assert sm.ms.fault == "motion_fault"
        fault_msg = only(msgs, None)
        assert fault_msg["type"] == "fault"
        assert fault_msg["reason"] == "motion_fault" and fault_msg["axis"] == "Y2"

    def test_jam_during_run_keeps_motion_fault_reason(self, sm):
        sm.ms.stored_program = {"version": 1,
                                "program": [{"op": "DELAY", "ms": 300},
                                            {"op": "HALT"}]}
        sm.ms.set_state(State.READY)
        sm.enqueue_button("start"); sm.tick()
        assert sm.ms.state == State.RUNNING
        sm.enqueue_jam("X"); sm.tick()
        assert sm.ms.state == State.FAULTED and sm.ms.fault == "motion_fault"
        # The aborting interpreter must not overwrite motion_fault with estop_triggered.
        sm._interp_thread.join(timeout=2.0)
        sm.tick()
        assert sm.ms.fault == "motion_fault"

    def test_reset_fault_rearms_stop_event(self, sm):
        sm.enqueue_jam("X"); sm.tick()
        drain(sm)
        sm.ms.set_state(State.FAULTED)   # ensure FAULTED for the command gate
        resp = only(step(sm, {"id": 1, "cmd": "reset_fault"}))
        assert resp["type"] == "ack"
        assert sm.ms.state == State.IDLE and not sm._stop_event.is_set()

    def test_estop_release_returns_to_idle(self, sm):
        sm.enqueue_estop(released=False); sm.tick()
        assert sm.ms.state == State.ESTOPPED
        sm.enqueue_estop(released=True); sm.tick()
        assert sm.ms.state == State.IDLE
        assert sm.ms.estop_hw is False and not sm._stop_event.is_set()

    def test_all_documented_faults_are_injectable(self, sm):
        for reason in simulator.VALID_FAULTS_SET:
            m = StateMachine()
            m.enqueue_fault(reason); m.tick()
            assert m.ms.state == State.FAULTED and m.ms.fault == reason


# ===========================================================================
# program_loaded status field
# ===========================================================================

class TestProgramLoadedStatus:

    def test_false_when_no_program(self, sm):
        assert sm._build_status()["program_loaded"] is False

    def test_true_after_program_stored(self, sm):
        sm.ms.stored_program = {"version": 1, "program": [{"op": "HALT"}]}
        assert sm._build_status()["program_loaded"] is True


# ===========================================================================
# SimulatedMachine — interpreter-facing backend
# ===========================================================================

@pytest.fixture
def fast(monkeypatch):
    """Patch out time.sleep so stepped motion/probe loops run instantly."""
    monkeypatch.setattr(simulator.time, "sleep", lambda *a, **k: None)


@pytest.fixture
def machine_and_state(_no_disk):
    import queue
    ms = MachineState()
    return SimulatedMachine(ms, queue.Queue()), ms


class TestSimulatedMachine:

    def test_get_position(self, machine_and_state):
        machine, ms = machine_and_state
        ms.x_mm, ms.y_mm, ms.z_mm = 1.0, 2.0, 3.0
        assert machine.get_position() == (1.0, 2.0, 3.0)

    def test_move_to_reaches_target(self, machine_and_state, fast):
        machine, ms = machine_and_state
        machine.move_to(10.0, 20.0, 30.0, threading.Event())
        assert (ms.x_mm, ms.y_mm, ms.z_mm) == (10.0, 20.0, 30.0)

    def test_move_to_aborts_on_stop_event(self, machine_and_state, fast):
        machine, ms = machine_and_state
        ev = threading.Event()
        ev.set()
        with pytest.raises(ProgramFault, match="estop_triggered"):
            machine.move_to(10.0, 20.0, 30.0, ev)

    def test_home_zeroes_only_requested_axes(self, machine_and_state, fast):
        machine, ms = machine_and_state
        ms.x_mm, ms.y_mm, ms.z_mm = 5.0, 6.0, 7.0
        machine.home(["X"], threading.Event())
        assert ms.x_mm == 0.0
        assert ms.y_mm == 6.0 and ms.z_mm == 7.0

    def test_probe_z_returns_near_home_surface(self, machine_and_state, fast):
        machine, ms = machine_and_state
        ms.home_surface_z = 10.0
        z = machine.probe_z(0.0, 0.0, approach_z=60.0, step_mm=1.0,
                            max_depth_mm=150.0, threshold_mm=5.0,
                            stop_event=threading.Event())
        assert 10.0 <= z <= 15.0

    def test_probe_z_uses_deposit_surface_near_deposit(self, machine_and_state,
                                                       fast):
        machine, ms = machine_and_state
        ms.taught["deposit"] = (50.0, 200.0, 5.0)
        ms.deposit_surface_z = 5.0
        z = machine.probe_z(50.0, 200.0, approach_z=60.0, step_mm=1.0,
                            max_depth_mm=150.0, threshold_mm=5.0,
                            stop_event=threading.Event())
        assert 5.0 <= z <= 10.0

    def test_set_output_routes_to_state(self, machine_and_state):
        machine, ms = machine_and_state
        machine.set_output("pump", True)
        machine.set_output("valve", True)
        machine.set_output("servo_door", "open")
        machine.set_output("servo_laser_btn", "press")
        assert ms.pump is True and ms.valve is True
        assert ms.servo_door == "open" and ms.servo_laser_btn == "press"

    def test_set_output_unknown_faults(self, machine_and_state):
        machine, _ = machine_and_state
        with pytest.raises(ProgramFault, match="unknown output"):
            machine.set_output("laser", True)

    def test_read_sensor_named(self, machine_and_state):
        machine, ms = machine_and_state
        ms.laser_head_home = True
        assert machine.read_sensor("laser_safe") is True
        ms.estop_hw = True
        assert machine.read_sensor("estop_hw") is True

    def test_read_sensor_tof_channel(self, machine_and_state):
        machine, ms = machine_and_state
        ms.tof_dist_mm[3] = 123
        assert machine.read_sensor("tof_ch3_mm") == 123.0

    def test_read_sensor_unknown_faults(self, machine_and_state):
        machine, _ = machine_and_state
        with pytest.raises(ProgramFault, match="unknown sensor"):
            machine.read_sensor("barometer")

    def test_delay_returns_immediately_for_zero(self, machine_and_state, fast):
        machine, _ = machine_and_state
        machine.delay(0, threading.Event())   # must not raise or hang

    def test_delay_aborts_on_stop_event(self, machine_and_state, fast):
        machine, _ = machine_and_state
        ev = threading.Event()
        ev.set()
        with pytest.raises(ProgramFault, match="estop_triggered"):
            machine.delay(500, ev)



# ===========================================================================
# Stepper calibration
# ===========================================================================

_jog_ids = itertools.count(900)

def _jog(sm, steps):
    """Jog-and-measure: send a cal_jog command to accumulate raw steps.

    Uses a unique id per call (as the real GUI does); the MCU/sim de-dup retried
    commands by id, so reusing one id would make the second jog look like a retry.
    """
    return step(sm, {"id": next(_jog_ids), "cmd": "cal_jog", "steps": steps})


class TestStepperCalibration:
    """Tests for calibrate_axis / set_cal_distance / get_param calibration keys."""

    # ---- command gating ----

    def test_calibrate_axis_accepted_in_idle(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        assert only(msgs)["type"] == "ack"

    def test_calibrate_axis_accepted_in_ready(self, sm):
        sm.ms.set_state(State.READY)
        msgs = step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "Y"})
        assert only(msgs)["type"] == "ack"

    def test_calibrate_axis_rejected_in_running(self, sm):
        sm.ms.set_state(State.RUNNING)
        msgs = step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "not_ready"

    def test_calibrate_axis_rejected_with_bad_axis(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "Q"})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "invalid_axis"

    def test_set_cal_distance_rejected_outside_calibrating(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "set_cal_distance", "axis": "X", "mm": 420.0})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "not_ready"

    def test_command_rejected_during_calibrating_with_correct_reason(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        msgs = step(sm, {"id": 2, "cmd": "home"})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "calibrating"

    def test_cancel_calibration_returns_to_idle_without_saving(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        _jog(sm, 5000)
        msgs = step(sm, {"id": 2, "cmd": "cancel_calibration"})
        r = only(msgs)
        assert r["type"] == "ack"
        assert sm.ms.state == State.IDLE
        assert sm.ms.cal_jog_steps == 0
        assert sm.ms.steps_per_mm.get("X", 0.0) == 0.0   # untouched

    def test_cancel_calibration_rejected_outside_calibrating(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "cancel_calibration"})
        r = only(msgs)
        assert r["type"] == "nack"

    # ---- state transitions ----

    def test_calibrate_enters_calibrating_state(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        assert sm.ms.state == State.CALIBRATING

    def test_calibrating_axis_stored(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "Z"})
        assert sm.ms.cal_axis == "Z"

    def test_cal_jog_accumulates_steps(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        assert sm.ms.cal_jog_steps == 0
        _jog(sm, 8000)
        _jog(sm, 4800)
        assert sm.ms.cal_jog_steps == 12800
        assert sm.ms.state == State.CALIBRATING   # still waiting for distance

    def test_cal_jog_accumulates_signed_net(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "Z"})
        _jog(sm, 6400)
        _jog(sm, -2000)
        assert sm.ms.cal_jog_steps == 4400   # net of out-and-back

    def test_duplicate_command_id_is_not_re_executed(self, sm):
        # A retried command (same id) must re-ack but not run twice.
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        step(sm, {"id": 7, "cmd": "cal_jog", "steps": 1000})
        assert sm.ms.cal_jog_steps == 1000
        msgs = step(sm, {"id": 7, "cmd": "cal_jog", "steps": 1000})  # resend
        assert only(msgs)["type"] == "ack"          # still acked
        assert sm.ms.cal_jog_steps == 1000          # not doubled

    def test_query_with_repeated_id_still_runs(self, sm):
        # Queries are exempt from de-dup — they must re-run for fresh data even
        # when an id repeats (would otherwise look like a retry).
        sm.ms.set_state(State.READY)
        m1 = step(sm, {"id": 5, "cmd": "query_status"})
        m2 = step(sm, {"id": 5, "cmd": "query_status"})
        assert any(x.get("type") == "status" for x in m1)
        assert any(x.get("type") == "status" for x in m2)   # not deduped

    def test_cal_jog_rejected_outside_calibrating(self, sm):
        sm.ms.set_state(State.READY)
        msgs = step(sm, {"id": 1, "cmd": "cal_jog", "steps": 1000})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "not_ready"

    def test_set_cal_distance_computes_steps_per_mm(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        _jog(sm, 12800)
        msgs = step(sm, {"id": 2, "cmd": "set_cal_distance", "axis": "X", "mm": 160.0})
        r = only(msgs)
        assert r["type"] == "ack"
        assert sm.ms.state == State.IDLE
        assert sm.ms.steps_per_mm["X"] == pytest.approx(80.0)

    def test_set_cal_distance_returns_to_idle(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "Y"})
        _jog(sm, 9000)
        step(sm, {"id": 2, "cmd": "set_cal_distance", "axis": "Y", "mm": 300.0})
        assert sm.ms.state == State.IDLE

    def test_set_cal_distance_rejects_before_jog(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        # No cal_jog yet — nothing to divide.
        msgs = step(sm, {"id": 2, "cmd": "set_cal_distance", "axis": "X", "mm": 100.0})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "no_jog_steps"

    def test_set_cal_distance_rejects_zero_distance(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        _jog(sm, 12800)
        msgs = step(sm, {"id": 2, "cmd": "set_cal_distance", "axis": "X", "mm": 0.0})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "invalid_distance"

    def test_axes_calibrated_independently(self, sm):
        for i, axis in enumerate(("X", "Y", "Z")):
            sm.ms.set_state(State.IDLE)
            sm.ms.cal_axis = ""
            sm.ms.cal_jog_steps = 0
            step(sm, {"id": 100 + i*2,   "cmd": "calibrate_axis", "axis": axis})
            _jog(sm, 10000)
            step(sm, {"id": 101 + i*2, "cmd": "set_cal_distance", "axis": axis, "mm": 100.0})
        for axis in ("X", "Y", "Z"):
            assert sm.ms.steps_per_mm[axis] > 0

    # ---- status broadcast ----

    def test_cal_fields_absent_when_not_calibrating(self, sm):
        status = sm._build_status()
        assert status.get("cal_axis") is None
        assert status.get("cal_steps") is None

    def test_cal_fields_present_during_jog(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        _jog(sm, 12800)
        status = sm._build_status()
        assert status["cal_axis"] == "X"
        assert status["cal_steps"] == 12800

    def test_state_is_calibrating_in_status(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        status = sm._build_status()
        assert status["state"] == "CALIBRATING"

    # ---- get_param ----

    def test_get_param_steps_per_mm_x_returns_value(self, sm):
        step(sm, {"id": 1, "cmd": "calibrate_axis", "axis": "X"})
        _jog(sm, 12800)
        step(sm, {"id": 2, "cmd": "set_cal_distance", "axis": "X", "mm": 200.0})
        msgs = step(sm, {"id": 3, "cmd": "get_param", "key": "steps_per_mm_x"})
        r = only(msgs, "get_param")
        assert r["type"] == "ack"
        assert r["value"] == pytest.approx(64.0)   # 12800 / 200

    def test_get_param_returns_zero_before_calibration(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "get_param", "key": "steps_per_mm_y"})
        r = only(msgs, "get_param")
        assert r["type"] == "ack"
        assert r["value"] == pytest.approx(0.0)


# ===========================================================================
# Soft travel limits  (set_max_travel + enforcement)
# ===========================================================================

class TestSoftTravelLimits:
    """Tests for set_max_travel and the soft-limit enforcement on motion."""

    # ---- set_max_travel command ----

    def test_set_max_travel_writes_limit(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "set_max_travel", "axis": "X", "mm": 250.0})
        assert only(msgs)["type"] == "ack"
        assert sm.ms.max_travel_mm["X"] == pytest.approx(250.0)

    def test_set_max_travel_accepted_in_ready(self, sm):
        sm.ms.set_state(State.READY)
        msgs = step(sm, {"id": 1, "cmd": "set_max_travel", "axis": "Z", "mm": 80.0})
        assert only(msgs)["type"] == "ack"

    def test_set_max_travel_rejected_in_running(self, sm):
        sm.ms.set_state(State.RUNNING)
        msgs = step(sm, {"id": 1, "cmd": "set_max_travel", "axis": "X", "mm": 250.0})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "not_ready"

    def test_set_tof_threshold_writes_and_reads_back(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "set_tof_threshold",
                         "key": "tof_max_sigma_mm", "mm": 10})
        assert only(msgs)["type"] == "ack"
        assert sm.ms.tof_max_sigma_mm == 10
        msgs = step(sm, {"id": 2, "cmd": "get_param", "key": "tof_max_sigma_mm"})
        assert only(msgs)["value"] == 10

    def test_set_tof_threshold_unknown_key_nacks(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "set_tof_threshold",
                         "key": "bogus", "mm": 5})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "unknown_key"


    def test_set_max_travel_rejects_bad_axis(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "set_max_travel", "axis": "Q", "mm": 250.0})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "invalid_axis"

    def test_set_max_travel_rejects_nonpositive(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "set_max_travel", "axis": "X", "mm": 0.0})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "invalid_travel"

    def test_set_max_travel_y1_y2_map_to_y(self, sm):
        step(sm, {"id": 1, "cmd": "set_max_travel", "axis": "Y1", "mm": 400.0})
        assert sm.ms.max_travel_mm["Y"] == pytest.approx(400.0)

    def test_get_param_reads_max_travel(self, sm):
        step(sm, {"id": 1, "cmd": "set_max_travel", "axis": "X", "mm": 250.0})
        msgs = step(sm, {"id": 2, "cmd": "get_param", "key": "max_travel_mm_x"})
        r = only(msgs, "get_param")
        assert r["type"] == "ack"
        assert r["value"] == pytest.approx(250.0)

    # ---- enforcement on the manual move_to command ----

    def _configure_envelope(self, sm, x=200.0, y=200.0, z=100.0):
        sm.ms.max_travel_mm = {"X": x, "Y": y, "Z": z}
        sm.ms.set_state(State.READY)

    def test_move_to_within_envelope_acked(self, sm):
        self._configure_envelope(sm)
        msgs = step(sm, {"id": 1, "cmd": "move_to", "x_mm": 150.0, "y_mm": 50.0, "z_mm": 10.0})
        assert only(msgs)["type"] == "ack"

    def test_move_to_outside_envelope_nacked(self, sm):
        self._configure_envelope(sm)
        msgs = step(sm, {"id": 1, "cmd": "move_to", "x_mm": 250.0, "y_mm": 50.0, "z_mm": 10.0})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "soft_limit_x"

    def test_move_to_negative_nacked(self, sm):
        self._configure_envelope(sm)
        msgs = step(sm, {"id": 1, "cmd": "move_to", "x_mm": 10.0, "y_mm": -5.0, "z_mm": 10.0})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "soft_limit_y"

    def test_move_to_unbounded_when_unconfigured(self, sm):
        # No limits set (all zero) → motion is unbounded.
        sm.ms.set_state(State.READY)
        msgs = step(sm, {"id": 1, "cmd": "move_to", "x_mm": 9999.0, "y_mm": 0.0, "z_mm": 0.0})
        assert only(msgs)["type"] == "ack"

    # ---- enforcement at the SimulatedMachine chokepoint (program MOVE path) ----

    def test_simulated_machine_move_to_faults_outside_envelope(self):
        ms = MachineState()
        ms.max_travel_mm = {"X": 200.0, "Y": 200.0, "Z": 100.0}
        machine = SimulatedMachine(ms, queue.Queue())
        with pytest.raises(ProgramFault) as exc:
            machine.move_to(250.0, 10.0, 10.0, threading.Event())
        assert exc.value.reason == "soft_limit_x"

    def test_simulated_machine_move_to_ok_inside_envelope(self):
        ms = MachineState()
        ms.max_travel_mm = {"X": 200.0, "Y": 200.0, "Z": 100.0}
        machine = SimulatedMachine(ms, queue.Queue())
        machine.move_to(150.0, 150.0, 50.0, threading.Event())   # must not raise
        assert ms.x_mm == pytest.approx(150.0)

    def test_travel_violation_helper(self):
        limits = {"X": 200.0, "Y": 200.0, "Z": 100.0}
        assert simulator.travel_violation(limits, 150, 150, 50) is None
        assert simulator.travel_violation(limits, 201, 0, 0) == "x"
        assert simulator.travel_violation(limits, 0, 0, 120) == "z"
        # Unconfigured (any axis zero) → unbounded.
        assert simulator.travel_violation({"X": 0, "Y": 0, "Z": 0}, 9999, 9999, 9999) is None


# ===========================================================================
# Sensor calibration
# ===========================================================================

class TestSensorCalibration:
    """Tests for calibrate_sensors command and tof_offset get_param keys."""

    # ---- gating ----

    def test_calibrate_sensors_accepted_in_idle(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        assert only(msgs)["type"] == "ack"

    def test_calibrate_sensors_accepted_in_ready(self, sm):
        sm.ms.set_state(State.READY)
        msgs = step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        assert only(msgs)["type"] == "ack"

    def test_calibrate_sensors_rejected_in_running(self, sm):
        sm.ms.set_state(State.RUNNING)
        msgs = step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        r = only(msgs)
        assert r["type"] == "nack"
        assert r["reason"] == "not_ready"

    # ---- reads current tof values ----

    def test_calibrate_sensors_reads_ch0_to_ch3(self, sm):
        sm.ms.tof_dist_mm = [45, 47, 46, 48, 30, 25]
        msgs = step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        r = only(msgs)
        assert r["type"] == "ack"
        assert r["offsets"] == [45, 47, 46, 48]

    def test_calibrate_sensors_does_not_include_ch4_ch5(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        r = only(msgs)
        assert len(r["offsets"]) == 4

    def test_calibrate_sensors_stores_offsets(self, sm):
        sm.ms.tof_dist_mm = [45, 47, 46, 48, 30, 25]
        step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        assert sm.ms.tof_offsets == [45, 47, 46, 48]

    def test_recalibrate_overwrites_previous(self, sm):
        sm.ms.tof_dist_mm = [45, 47, 46, 48, 30, 25]
        step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        sm.ms.tof_dist_mm = [50, 51, 49, 52, 30, 25]
        step(sm, {"id": 2, "cmd": "calibrate_sensors"})
        assert sm.ms.tof_offsets == [50, 51, 49, 52]

    # ---- get_param for tof offsets ----

    def test_get_param_tof_offset_returns_none_before_cal(self, sm):
        msgs = step(sm, {"id": 1, "cmd": "get_param", "key": "tof_offset_0"})
        r = only(msgs, "get_param")
        assert r["type"] == "ack"
        assert r["value"] is None

    def test_get_param_tof_offset_returns_stored_value(self, sm):
        sm.ms.tof_dist_mm = [45, 47, 46, 48, 30, 25]
        step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        for ch, expected in enumerate([45, 47, 46, 48]):
            msgs = step(sm, {"id": ch + 2, "cmd": "get_param",
                              "key": f"tof_offset_{ch}"})
            r = only(msgs, "get_param")
            assert r["value"] == expected, f"ch{ch}: expected {expected}, got {r['value']}"

    def test_get_param_tof_offset_all_four_channels(self, sm):
        sm.ms.tof_dist_mm = [40, 41, 42, 43, 30, 25]
        step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        for ch in range(4):
            msgs = step(sm, {"id": ch + 2, "cmd": "get_param",
                              "key": f"tof_offset_{ch}"})
            r = only(msgs, "get_param")
            assert r["value"] == 40 + ch

    # ---- status sensors (ch4/ch5) unaffected ----

    def test_calibrate_sensors_does_not_change_ch4_reading(self, sm):
        sm.ms.tof_dist_mm[4] = 30
        step(sm, {"id": 1, "cmd": "calibrate_sensors"})
        assert sm.ms.tof_dist_mm[4] == 30

    # ---- initial offsets are None (not calibrated) ----

    def test_initial_offsets_are_none(self, sm):
        assert all(v is None for v in sm.ms.tof_offsets)