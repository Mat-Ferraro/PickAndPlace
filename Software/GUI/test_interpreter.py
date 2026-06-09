"""
Unit tests for interpreter.py — ProgramValidator and ProgramInterpreter.

These tests treat the interpreter as the executable specification for the job
program instruction set defined in job-program.md. Because the interpreter is
decoupled from hardware via MachineInterface, every behavior pinned down here
is a behavior the eventual C++ port on the Mega must reproduce.

Run from the Software/ directory:  pytest -q
"""

import threading

import pytest

from interpreter import (
    ProgramFault,
    ProgramInterpreter,
    ProgramValidator,
)


# ===========================================================================
# ProgramValidator
# ===========================================================================

class TestProgramValidator:

    def setup_method(self):
        self.v = ProgramValidator()

    def _minimal(self, **over):
        prog = {"version": 1, "program": [{"op": "HALT"}]}
        prog.update(over)
        return prog

    def test_minimal_valid_program_passes(self):
        ok, err = self.v.validate(self._minimal())
        assert ok and err == ""

    def test_non_dict_program_rejected(self):
        ok, err = self.v.validate([1, 2, 3])
        assert not ok and "JSON object" in err

    def test_missing_version_rejected(self):
        ok, err = self.v.validate({"program": [{"op": "HALT"}]})
        assert not ok and "version" in err

    def test_wrong_version_rejected(self):
        ok, err = self.v.validate({"version": 2, "program": [{"op": "HALT"}]})
        assert not ok and "version" in err

    def test_missing_program_array_rejected(self):
        ok, err = self.v.validate({"version": 1})
        assert not ok and "program" in err

    def test_program_not_a_list_rejected(self):
        ok, err = self.v.validate({"version": 1, "program": {"op": "HALT"}})
        assert not ok and "program" in err

    def test_instruction_must_be_object(self):
        ok, err = self.v.validate(self._minimal(program=["HALT"]))
        assert not ok and "must be an object" in err

    def test_missing_op_rejected(self):
        ok, err = self.v.validate(self._minimal(program=[{"x": 1}]))
        assert not ok and "missing 'op'" in err

    def test_unknown_op_rejected(self):
        ok, err = self.v.validate(self._minimal(program=[{"op": "TELEPORT"}]))
        assert not ok and "unknown op" in err

    def test_missing_required_field_rejected(self):
        # MOVE requires x, y, z
        ok, err = self.v.validate(self._minimal(
            program=[{"op": "MOVE", "x": 1, "y": 2}]))
        assert not ok and "missing required field 'z'" in err

    def test_probe_z_approach_z_is_optional(self):
        ok, err = self.v.validate(self._minimal(
            program=[{"op": "PROBE_Z", "x": 1, "y": 2, "store": "s"}]))
        assert ok, err

    def test_call_to_unknown_subroutine_rejected(self):
        ok, err = self.v.validate(self._minimal(
            program=[{"op": "CALL", "sub": "nope"}]))
        assert not ok and "unknown subroutine" in err

    def test_call_to_known_subroutine_passes(self):
        ok, err = self.v.validate(self._minimal(
            program=[{"op": "CALL", "sub": "pick"}],
            subroutines={"pick": [{"op": "RETURN"}]}))
        assert ok, err

    def test_nested_body_is_validated(self):
        # Bad instruction nested inside an IF/then must be caught.
        ok, err = self.v.validate(self._minimal(program=[{
            "op": "IF", "condition": "true",
            "then": [{"op": "MOVE", "x": 1, "y": 2}],   # missing z
        }]))
        assert not ok and "missing required field 'z'" in err

    def test_subroutine_bodies_are_validated(self):
        ok, err = self.v.validate(self._minimal(
            program=[{"op": "CALL", "sub": "bad"}],
            subroutines={"bad": [{"op": "WAT"}]}))
        assert not ok and "unknown op" in err


# ===========================================================================
# ProgramInterpreter — variables, resolution, expansion
# ===========================================================================

class TestVariables:

    def test_set_var_literal_preserves_type(self, make_interp):
        interp = make_interp([{"op": "SET_VAR", "name": "n", "value": 5}])
        interp.run()
        assert interp.status["variables"]["n"] == 5

    def test_set_var_expr_evaluates_to_float(self, make_interp):
        interp = make_interp([
            {"op": "SET_VAR", "name": "x", "value": 5},
            {"op": "SET_VAR", "name": "y", "expr": "$x * 2"},
        ])
        interp.run()
        assert interp.status["variables"]["y"] == 10.0

    def test_dollar_var_resolves_in_move(self, make_interp, machine):
        interp = make_interp([
            {"op": "SET_VAR", "name": "tx", "value": 12.0},
            {"op": "MOVE", "x": "$tx", "y": 0.0, "z": 0.0},
        ])
        interp.run()
        assert machine.moves[-1][:3] == (12.0, 0.0, 0.0)

    def test_undefined_variable_faults(self, make_interp):
        interp = make_interp([{"op": "MOVE", "x": "$missing", "y": 0, "z": 0}])
        with pytest.raises(ProgramFault, match="undefined variable"):
            interp.run()

    def test_log_expands_variables(self, make_interp, machine):
        interp = make_interp([
            {"op": "SET_VAR", "name": "count", "value": 7},
            {"op": "LOG", "message": "did $count items"},
        ])
        interp.run()
        assert machine.logs == ["did 7 items"]

    def test_log_renders_whole_floats_as_ints(self, make_interp, machine):
        interp = make_interp([
            {"op": "SET_VAR", "name": "n", "value": 0},
            {"op": "SET_VAR", "name": "n", "expr": "$n + 1"},   # -> 1.0
            {"op": "LOG", "message": "sheet $n done"},
        ])
        interp.run()
        assert machine.logs == ["sheet 1 done"]

    def test_unsafe_expression_faults(self, make_interp):
        interp = make_interp([
            {"op": "SET_VAR", "name": "x", "expr": "__import__('os')"},
        ])
        with pytest.raises(ProgramFault, match="unsafe expression"):
            interp.run()


# ===========================================================================
# ProgramInterpreter — motion ops
# ===========================================================================

class TestMotion:

    def test_move_uses_config_default_speed(self, make_interp, machine):
        interp = make_interp(
            [{"op": "MOVE", "x": 1, "y": 2, "z": 3}],
            config={"default_speed_pct": 55})
        interp.run()
        assert machine.moves == [(1, 2, 3, 55)]

    def test_move_speed_override(self, make_interp, machine):
        interp = make_interp([{"op": "MOVE", "x": 1, "y": 2, "z": 3,
                               "speed": 30}])
        interp.run()
        assert machine.moves[-1][3] == 30

    def test_move_default_speed_is_80_without_config(self, make_interp,
                                                     machine):
        interp = make_interp([{"op": "MOVE", "x": 0, "y": 0, "z": 0}])
        interp.run()
        assert machine.moves[-1][3] == 80

    def test_move_visits_waypoints_in_order(self, make_interp, machine):
        interp = make_interp([{
            "op": "MOVE", "x": 10, "y": 10, "z": 10,
            "via": [{"x": 1, "y": 1, "z": 1}, {"x": 5, "y": 5, "z": 5}],
        }])
        interp.run()
        assert [m[:3] for m in machine.moves] == [
            (1, 1, 1), (5, 5, 5), (10, 10, 10)]

    def test_home_passes_axes_through(self, make_interp, machine):
        interp = make_interp([{"op": "HOME", "axes": ["X", "Y", "Z"]}])
        interp.run()
        assert machine.homes == [["X", "Y", "Z"]]


class TestProbeZ:

    def test_probe_stores_result(self, make_interp, machine):
        machine.probe_result = 12.5
        interp = make_interp([{"op": "PROBE_Z", "x": 1, "y": 2,
                               "approach_z": 60.0, "store": "surf"}])
        interp.run()
        assert interp.status["variables"]["surf"] == 12.5

    def test_probe_uses_explicit_approach_z(self, make_interp, machine):
        interp = make_interp([{"op": "PROBE_Z", "x": 1, "y": 2,
                               "approach_z": 60.0, "store": "s"}])
        interp.run()
        assert machine.probes[-1][2] == 60.0

    def test_probe_defaults_approach_z_to_current_z(self, make_interp,
                                                    machine):
        machine.position = (0.0, 0.0, 42.0)
        interp = make_interp([{"op": "PROBE_Z", "x": 1, "y": 2, "store": "s"}])
        interp.run()
        assert machine.probes[-1][2] == 42.0

    def test_probe_passes_config_tuning(self, make_interp, machine):
        interp = make_interp(
            [{"op": "PROBE_Z", "x": 1, "y": 2, "approach_z": 60, "store": "s"}],
            config={"probe_step_mm": 0.25, "probe_max_depth_mm": 100.0,
                    "probe_threshold_mm": 3.0})
        interp.run()
        _, _, _, step, max_depth, thresh = machine.probes[-1]
        assert (step, max_depth, thresh) == (0.25, 100.0, 3.0)


# ===========================================================================
# ProgramInterpreter — I/O ops
# ===========================================================================

class TestIO:

    def test_output_passthrough(self, make_interp, machine):
        interp = make_interp([
            {"op": "OUTPUT", "name": "pump", "value": True},
            {"op": "OUTPUT", "name": "servo_door", "value": "open"},
        ])
        interp.run()
        assert machine.outputs == [("pump", True), ("servo_door", "open")]

    def test_read_sensor_stores_value(self, make_interp, machine):
        machine.sensors["pickup_ok"] = True
        interp = make_interp([{"op": "READ_SENSOR", "sensor": "pickup_ok",
                               "store": "ok"}])
        interp.run()
        assert interp.status["variables"]["ok"] is True

    def test_delay_passes_milliseconds(self, make_interp, machine):
        interp = make_interp([{"op": "DELAY", "ms": 250}])
        interp.run()
        assert machine.delays == [250.0]


# ===========================================================================
# ProgramInterpreter — conditions
# ===========================================================================

class TestConditions:

    def _eval(self, make_interp, machine, condition, sensors=None,
              variables=None):
        """Run an IF that logs 'yes' iff the condition is true."""
        if sensors:
            machine.sensors.update(sensors)
        setup = [{"op": "SET_VAR", "name": k, "value": v}
                 for k, v in (variables or {}).items()]
        interp = make_interp(setup + [{
            "op": "IF", "condition": condition,
            "then": [{"op": "LOG", "message": "yes"}],
        }])
        interp.run()
        return machine.logs == ["yes"]

    def test_literal_true(self, make_interp, machine):
        assert self._eval(make_interp, machine, "true")

    def test_literal_false(self, make_interp, machine):
        assert not self._eval(make_interp, machine, "false")

    def test_not_operator(self, make_interp, machine):
        assert self._eval(make_interp, machine, "not false")

    def test_sensor_condition_true(self, make_interp, machine):
        assert self._eval(make_interp, machine, "material_present",
                          sensors={"material_present": True})

    def test_sensor_condition_false(self, make_interp, machine):
        assert not self._eval(make_interp, machine, "material_present",
                              sensors={"material_present": False})

    def test_not_sensor(self, make_interp, machine):
        assert self._eval(make_interp, machine, "not material_present",
                          sensors={"material_present": False})

    @pytest.mark.parametrize("cond,expected", [
        ("$n == 3", True),
        ("$n != 3", False),
        ("$n >= 3", True),
        ("$n <= 3", True),
        ("$n > 2", True),
        ("$n < 3", False),
    ])
    def test_numeric_comparisons(self, make_interp, machine, cond, expected):
        assert self._eval(make_interp, machine, cond,
                          variables={"n": 3}) is expected

    def test_boolean_rhs_comparison(self, make_interp, machine):
        assert self._eval(make_interp, machine, "$flag == true",
                          variables={"flag": True})

    def test_comparison_on_undefined_var_faults(self, make_interp):
        interp = make_interp([{
            "op": "IF", "condition": "$ghost > 1",
            "then": [{"op": "HALT"}],
        }])
        with pytest.raises(ProgramFault, match="undefined variable"):
            interp.run()

    def test_invalid_condition_faults(self, make_interp):
        interp = make_interp([{
            "op": "IF", "condition": "wibble wobble",
            "then": [{"op": "HALT"}],
        }])
        with pytest.raises(ProgramFault, match="invalid condition"):
            interp.run()


# ===========================================================================
# ProgramInterpreter — flow control
# ===========================================================================

class TestFlowControl:

    def test_if_then_branch(self, make_interp, machine):
        interp = make_interp([{
            "op": "IF", "condition": "true",
            "then": [{"op": "LOG", "message": "T"}],
            "else": [{"op": "LOG", "message": "F"}],
        }])
        interp.run()
        assert machine.logs == ["T"]

    def test_if_else_branch(self, make_interp, machine):
        interp = make_interp([{
            "op": "IF", "condition": "false",
            "then": [{"op": "LOG", "message": "T"}],
            "else": [{"op": "LOG", "message": "F"}],
        }])
        interp.run()
        assert machine.logs == ["F"]

    def test_if_without_else_is_noop_when_false(self, make_interp, machine):
        interp = make_interp([{
            "op": "IF", "condition": "false",
            "then": [{"op": "LOG", "message": "T"}],
        }])
        interp.run()
        assert machine.logs == []

    def test_loop_for_runs_count_times(self, make_interp, machine):
        interp = make_interp([{
            "op": "LOOP_FOR", "count": 3,
            "body": [{"op": "LOG", "message": "tick"}],
        }])
        interp.run()
        assert machine.logs == ["tick", "tick", "tick"]

    def test_loop_for_exposes_loop_index(self, make_interp, machine):
        interp = make_interp([{
            "op": "LOOP_FOR", "count": 2,
            "body": [{"op": "LOG", "message": "i=$_loop_i"}],
        }])
        interp.run()
        assert machine.logs == ["i=0", "i=1"]

    def test_loop_while_terminates_on_condition(self, make_interp, machine):
        interp = make_interp([
            {"op": "SET_VAR", "name": "n", "value": 0},
            {"op": "LOOP_WHILE", "condition": "$n < 3", "body": [
                {"op": "LOG", "message": "x"},
                {"op": "SET_VAR", "name": "n", "expr": "$n + 1"},
            ]},
        ])
        interp.run()
        assert machine.logs == ["x", "x", "x"]

    def test_loop_while_overflow_faults(self, make_interp):
        # Condition never goes false -> MAX_LOOP_ITER guard must trip.
        interp = make_interp([{
            "op": "LOOP_WHILE", "condition": "true", "body": [],
        }])
        with pytest.raises(ProgramFault, match="loop_overflow"):
            interp.run()

    def test_halt_stops_execution(self, make_interp, machine):
        interp = make_interp([
            {"op": "LOG", "message": "before"},
            {"op": "HALT"},
            {"op": "LOG", "message": "after"},
        ])
        interp.run()
        assert machine.logs == ["before"]

    def test_fault_raises_with_expanded_reason(self, make_interp):
        interp = make_interp([
            {"op": "SET_VAR", "name": "code", "value": 7},
            {"op": "FAULT", "reason": "bad_$code"},
        ])
        with pytest.raises(ProgramFault) as exc:
            interp.run()
        assert exc.value.reason == "bad_7"

    def test_jump_is_unsupported_in_v1(self, make_interp):
        interp = make_interp([{"op": "JUMP", "to": "label"}])
        with pytest.raises(ProgramFault, match="JUMP is not supported"):
            interp.run()


# ===========================================================================
# ProgramInterpreter — subroutines (CALL / RETURN)
# ===========================================================================

class TestSubroutines:

    def test_call_executes_subroutine(self, make_interp, machine):
        interp = make_interp(
            [{"op": "CALL", "sub": "greet"}],
            subroutines={"greet": [{"op": "LOG", "message": "hi"}]})
        interp.run()
        assert machine.logs == ["hi"]

    def test_return_exits_subroutine_early(self, make_interp, machine):
        interp = make_interp(
            [{"op": "CALL", "sub": "s"}, {"op": "LOG", "message": "after"}],
            subroutines={"s": [
                {"op": "LOG", "message": "in"},
                {"op": "RETURN"},
                {"op": "LOG", "message": "unreached"},
            ]})
        interp.run()
        assert machine.logs == ["in", "after"]

    def test_call_unknown_subroutine_faults(self, make_interp):
        interp = make_interp([{"op": "CALL", "sub": "ghost"}])
        with pytest.raises(ProgramFault, match="unknown subroutine"):
            interp.run()

    def test_call_depth_limit_faults(self, make_interp):
        # A subroutine that calls itself must trip MAX_CALL_DEPTH, not recurse
        # forever.
        interp = make_interp(
            [{"op": "CALL", "sub": "rec"}],
            subroutines={"rec": [{"op": "CALL", "sub": "rec"}]})
        with pytest.raises(ProgramFault, match="call_depth"):
            interp.run()


# ===========================================================================
# ProgramInterpreter — WAIT
# ===========================================================================

class TestWait:

    def test_wait_returns_immediately_when_satisfied(self, make_interp,
                                                     machine):
        machine.sensors["laser_safe"] = True
        interp = make_interp([{"op": "WAIT", "condition": "laser_safe",
                               "timeout_ms": 1000}])
        interp.run()  # must not block

    def test_wait_times_out_with_custom_fault(self, make_interp, machine):
        machine.sensors["laser_safe"] = False
        interp = make_interp([{
            "op": "WAIT", "condition": "laser_safe",
            "timeout_ms": 10, "timeout_fault": "laser_interlock",
        }])
        with pytest.raises(ProgramFault, match="laser_interlock"):
            interp.run()

    def test_wait_satisfied_after_sensor_flips(self, make_interp, machine):
        # Sensor reads False the first time, True thereafter.
        calls = {"n": 0}

        def flip(_sensor):
            calls["n"] += 1
            return calls["n"] > 1

        machine.on_read_sensor = flip
        interp = make_interp([{"op": "WAIT", "condition": "laser_safe",
                               "timeout_ms": 5000}])
        interp.run()
        assert calls["n"] >= 2


# ===========================================================================
# ProgramInterpreter — pause / stop (cooperative cancellation)
# ===========================================================================

class TestPauseStop:

    def test_stop_event_aborts_before_next_instruction(self, make_interp,
                                                        events, machine):
        _pause, stop = events
        stop.set()
        interp = make_interp([{"op": "LOG", "message": "never"}])
        with pytest.raises(ProgramFault, match="estop_triggered"):
            interp.run()
        assert machine.logs == []

    def test_stop_during_pause_aborts(self, make_interp, events):
        pause, stop = events
        pause.set()
        stop.set()
        interp = make_interp([{"op": "LOG", "message": "x"}])
        with pytest.raises(ProgramFault, match="estop_triggered"):
            interp.run()

    def test_runs_to_completion_when_not_stopped(self, make_interp, machine):
        interp = make_interp([{"op": "LOG", "message": "done"},
                              {"op": "HALT"}])
        interp.run()
        assert machine.logs == ["done"]


# ===========================================================================
# ProgramInterpreter — status reporting
# ===========================================================================

class TestStatus:

    def test_status_reports_current_op_and_vars(self, make_interp):
        interp = make_interp([{"op": "SET_VAR", "name": "a", "value": 1}])
        interp.run()
        st = interp.status
        assert st["variables"] == {"a": 1}
        assert st["step_index"] >= 1

    def test_status_is_a_snapshot_copy(self, make_interp):
        interp = make_interp([{"op": "SET_VAR", "name": "a", "value": 1}])
        interp.run()
        snap = interp.status["variables"]
        snap["a"] = 999
        # Mutating the snapshot must not corrupt interpreter state.
        assert interp.status["variables"]["a"] == 1
