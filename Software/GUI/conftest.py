"""
Shared pytest fixtures and test doubles for the Pick-and-Place test suite.

The Software/ modules use flat imports (e.g. `from interpreter import ...`),
so we put the package root on sys.path here rather than turning Software/ into
an installed package. Run the suite with `pytest` from the Software/ directory.
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interpreter import MachineInterface, ProgramFault  # noqa: E402


class FakeMachine(MachineInterface):
    """
    A recording, fully-scriptable MachineInterface for interpreter unit tests.

    It does NOT sleep, move in steps, or touch hardware — every operation is
    instantaneous and recorded so tests can assert on the exact call sequence
    the interpreter produced. This is what keeps interpreter tests fast and
    deterministic, and it documents the contract the C++ port must satisfy.

    Scripting:
        machine.position      = (x, y, z)        # what get_position() returns
        machine.sensors[name] = value            # what read_sensor() returns
        machine.probe_result  = float            # what probe_z() returns
        machine.on_read_sensor = callable(name)  # optional: dynamic sensor values
                                                 # (e.g. flip a condition mid-WAIT)

    Recording (each is a list, in call order):
        machine.moves    -> (x, y, z, speed_pct)
        machine.probes   -> (x, y, approach_z, step, max_depth, threshold)
        machine.homes    -> [axes]
        machine.outputs  -> (name, value)
        machine.delays   -> ms
        machine.logs     -> message
        machine.reads    -> sensor name
    """

    def __init__(self):
        self.position = (0.0, 0.0, 0.0)
        self.sensors = {}
        self.probe_result = 0.0
        self.on_read_sensor = None

        self.moves = []
        self.probes = []
        self.homes = []
        self.outputs = []
        self.delays = []
        self.logs = []
        self.reads = []

    # ---- MachineInterface implementation ---------------------------------

    def get_position(self):
        return self.position

    def move_to(self, x, y, z, stop_event, speed_pct=80):
        if stop_event.is_set():
            raise ProgramFault("estop_triggered")
        self.moves.append((x, y, z, speed_pct))
        self.position = (x, y, z)

    def probe_z(self, x, y, approach_z, step_mm, max_depth_mm, threshold_mm,
                stop_event):
        if stop_event.is_set():
            raise ProgramFault("estop_triggered")
        self.probes.append((x, y, approach_z, step_mm, max_depth_mm,
                            threshold_mm))
        self.position = (x, y, self.probe_result)
        return self.probe_result

    def home(self, axes, stop_event):
        if stop_event.is_set():
            raise ProgramFault("estop_triggered")
        self.homes.append(list(axes))

    def set_output(self, name, value):
        self.outputs.append((name, value))

    def read_sensor(self, sensor):
        self.reads.append(sensor)
        if self.on_read_sensor is not None:
            return self.on_read_sensor(sensor)
        return self.sensors.get(sensor, False)

    def delay(self, ms, stop_event):
        if stop_event.is_set():
            raise ProgramFault("estop_triggered")
        self.delays.append(ms)

    def log(self, message):
        self.logs.append(message)


@pytest.fixture
def machine():
    """A fresh recording FakeMachine for each test."""
    return FakeMachine()


@pytest.fixture
def events():
    """A fresh (pause_event, stop_event) pair, both clear."""
    return threading.Event(), threading.Event()


@pytest.fixture
def make_interp(machine, events):
    """
    Factory: build a ProgramInterpreter around a program body.

    Usage:
        interp = make_interp(program=[{...}, {...}], subroutines={...},
                             config={...})
        interp.run()

    `program` may be either a full program dict (with a 'program' key) or just
    the instruction list, which is wrapped for you.
    """
    from interpreter import ProgramInterpreter

    pause_event, stop_event = events

    def _build(program, subroutines=None, config=None):
        if isinstance(program, list):
            prog = {"version": 1, "program": program}
        else:
            prog = dict(program)
        if subroutines is not None:
            prog["subroutines"] = subroutines
        if config is not None:
            prog["config"] = config
        return ProgramInterpreter(prog, machine, pause_event, stop_event)

    return _build
