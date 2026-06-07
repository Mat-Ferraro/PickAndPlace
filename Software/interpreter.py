"""
interpreter.py — Job Program Interpreter
Implements the instruction set defined in job-program.md.

The interpreter is deliberately decoupled from hardware: all physical
operations go through MachineInterface. This lets the same interpreter
run against the Python simulator and later be ported to C++ on the Mega.
"""

import re
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ProgramFault(Exception):
    """Runtime fault — transitions machine to FAULTED."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

class _HaltSignal(Exception):
    """Internal: HALT instruction reached."""

class _ReturnSignal(Exception):
    """Internal: RETURN instruction reached."""


# ---------------------------------------------------------------------------
# Machine interface
# ---------------------------------------------------------------------------

class MachineInterface(ABC):
    """
    Abstract interface between the interpreter and the physical (or simulated)
    machine. All blocking operations accept a stop_event; they must raise
    ProgramFault('estop_triggered') if the event fires during execution.
    """

    @abstractmethod
    def move_to(self, x: float, y: float, z: float,
                stop_event: threading.Event, speed_pct: int = 80) -> None: ...

    @abstractmethod
    def probe_z(self, x: float, y: float, approach_z: float,
                step_mm: float, max_depth_mm: float, threshold_mm: float,
                stop_event: threading.Event) -> float:
        """Descend from approach_z until surface detected. Returns confirmed Z."""
        ...

    @abstractmethod
    def home(self, axes: list, stop_event: threading.Event) -> None: ...

    @abstractmethod
    def set_output(self, name: str, value: Any) -> None: ...

    @abstractmethod
    def read_sensor(self, sensor: str) -> Any: ...

    @abstractmethod
    def delay(self, ms: float, stop_event: threading.Event) -> None: ...

    @abstractmethod
    def log(self, message: str) -> None: ...


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class ProgramValidator:
    """Validates a program dict before it is stored or executed."""

    _REQUIRED: dict = {
        'MOVE':       ['x', 'y', 'z'],
        'PROBE_Z':    ['x', 'y', 'approach_z', 'store'],
        'HOME':       ['axes'],
        'OUTPUT':     ['name', 'value'],
        'READ_SENSOR':['sensor', 'store'],
        'WAIT':       ['condition'],
        'DELAY':      ['ms'],
        'LOOP_WHILE': ['condition', 'body'],
        'LOOP_FOR':   ['count', 'body'],
        'IF':         ['condition', 'then'],
        'CALL':       ['sub'],
        'RETURN':     [],
        'HALT':       [],
        'FAULT':      ['reason'],
        'SET_VAR':    ['name'],
        'LOG':        ['message'],
        'LABEL':      ['name'],
        'JUMP':       ['to'],
    }

    def validate(self, program: dict) -> tuple:
        """Returns (ok: bool, error: str)."""
        if not isinstance(program, dict):
            return False, "Program must be a JSON object"
        if program.get('version') != 1:
            return False, "Missing or unsupported 'version' (expected 1)"
        if 'program' not in program or not isinstance(program['program'], list):
            return False, "Missing or invalid 'program' array"

        subs = program.get('subroutines', {})
        errors: list = []
        self._check_body(program['program'], subs, errors, "")
        for name, body in subs.items():
            self._check_body(body, subs, errors, f"subroutine '{name}': ")
            if errors:
                break

        return (True, "") if not errors else (False, errors[0])

    def _check_body(self, instrs, subs, errors, prefix):
        if not isinstance(instrs, list):
            errors.append(f"{prefix}body must be an array"); return
        for i, instr in enumerate(instrs):
            if errors: return
            loc = f"{prefix}instruction {i}"
            if not isinstance(instr, dict):
                errors.append(f"{loc}: must be an object"); continue
            op = instr.get('op')
            if not op:
                errors.append(f"{loc}: missing 'op'"); continue
            if op not in self._REQUIRED:
                errors.append(f"{loc}: unknown op '{op}'"); continue
            for field in self._REQUIRED[op]:
                if field not in instr:
                    errors.append(f"{loc}: {op} missing required field '{field}'")
            if op == 'CALL':
                sub = instr.get('sub')
                if sub and sub not in subs:
                    errors.append(f"{loc}: CALL references unknown subroutine '{sub}'")
            for nested in ('body', 'then', 'else'):
                if nested in instr:
                    self._check_body(instr[nested], subs, errors,
                                     f"{loc}/{nested}: ")


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------

class ProgramInterpreter:
    """
    Executes a validated program dict.

    Run in a dedicated thread:
        interp = ProgramInterpreter(program, machine, pause_event, stop_event)
        thread = Thread(target=interp.run, daemon=True)
        thread.start()

    Pause:  pause_event.set()
    Resume: pause_event.clear()
    Abort:  stop_event.set()

    On normal completion, run() returns.
    On fault, run() raises ProgramFault.
    """

    MAX_LOOP_ITER  = 10_000
    MAX_CALL_DEPTH = 8

    def __init__(self, program: dict, machine: MachineInterface,
                 pause_event: threading.Event, stop_event: threading.Event):
        self._prog       = program
        self._cfg        = program.get('config', {})
        self._subs       = program.get('subroutines', {})
        self._machine    = machine
        self._pause      = pause_event
        self._stop       = stop_event

        self._vars: dict       = {}
        self._call_depth: int  = 0
        self._loop_iter: Optional[int] = None

        self._lock       = threading.Lock()
        self._step_index = 0
        self._current_op: Optional[str] = None

    # ---- Public API -------------------------------------------------------

    def run(self) -> None:
        """Blocking. Raises ProgramFault on error, returns normally on HALT."""
        try:
            self._exec_body(self._prog['program'])
        except (_HaltSignal, _ReturnSignal):
            pass

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                'current_op': self._current_op,
                'step_index': self._step_index,
                'loop_iter':  self._loop_iter,
                'variables':  dict(self._vars),
            }

    # ---- Execution core ---------------------------------------------------

    def _exec_body(self, instructions: list) -> None:
        for instr in instructions:
            self._check()
            self._exec_one(instr)

    def _exec_one(self, instr: dict) -> None:
        op = instr['op']
        with self._lock:
            self._current_op = op
            self._step_index += 1

        dispatch = {
            'MOVE':        self._exec_move,
            'PROBE_Z':     self._exec_probe_z,
            'HOME':        self._exec_home,
            'OUTPUT':      self._exec_output,
            'READ_SENSOR': self._exec_read_sensor,
            'WAIT':        self._exec_wait,
            'DELAY':       self._exec_delay,
            'LOOP_WHILE':  self._exec_loop_while,
            'LOOP_FOR':    self._exec_loop_for,
            'IF':          self._exec_if,
            'CALL':        self._exec_call,
            'SET_VAR':     self._exec_set_var,
            'LOG':         self._exec_log,
            'LABEL':       lambda i: None,    # noop (target for JUMP)
            'JUMP':        self._exec_jump,
            'RETURN':      lambda i: (_ for _ in ()).throw(_ReturnSignal()),
            'HALT':        lambda i: (_ for _ in ()).throw(_HaltSignal()),
            'FAULT':       lambda i: (_ for _ in ()).throw(
                                ProgramFault(self._expand(i.get('reason', 'program_fault')))),
        }

        handler = dispatch.get(op)
        if handler is None:
            raise ProgramFault(f'program_error: unknown op {op!r}')
        handler(instr)

    # ---- Motion -----------------------------------------------------------

    def _exec_move(self, instr: dict) -> None:
        speed = instr.get('speed', self._cfg.get('default_speed_pct', 80))
        for wp in instr.get('via', []):
            self._machine.move_to(
                self._resolve(wp['x']), self._resolve(wp['y']),
                self._resolve(wp['z']), self._stop, speed)
            self._check()
        self._machine.move_to(
            self._resolve(instr['x']), self._resolve(instr['y']),
            self._resolve(instr['z']), self._stop, speed)

    def _exec_probe_z(self, instr: dict) -> None:
        z = self._machine.probe_z(
            self._resolve(instr['x']),
            self._resolve(instr['y']),
            self._resolve(instr['approach_z']),
            self._cfg.get('probe_step_mm', 0.5),
            self._cfg.get('probe_max_depth_mm', 200.0),
            self._cfg.get('probe_threshold_mm', 40.0),
            self._stop,
        )
        self._vars[instr['store']] = z

    def _exec_home(self, instr: dict) -> None:
        self._machine.home(instr['axes'], self._stop)

    # ---- I/O --------------------------------------------------------------

    def _exec_output(self, instr: dict) -> None:
        self._machine.set_output(instr['name'], instr['value'])

    def _exec_read_sensor(self, instr: dict) -> None:
        self._vars[instr['store']] = self._machine.read_sensor(instr['sensor'])

    # ---- Wait / timing ----------------------------------------------------

    def _exec_wait(self, instr: dict) -> None:
        condition    = instr['condition']
        timeout_ms   = instr.get('timeout_ms')
        timeout_fault = instr.get('timeout_fault', 'wait_timeout')
        start        = time.monotonic()

        while not self._eval_condition(condition):
            self._check()
            if timeout_ms is not None:
                if (time.monotonic() - start) * 1000 >= timeout_ms:
                    raise ProgramFault(timeout_fault)
            time.sleep(0.05)

    def _exec_delay(self, instr: dict) -> None:
        self._machine.delay(float(self._resolve(instr['ms'])), self._stop)

    # ---- Flow control -----------------------------------------------------

    def _exec_loop_while(self, instr: dict) -> None:
        condition = instr['condition']
        body      = instr['body']
        iterations = 0

        while self._eval_condition(condition):
            if iterations >= self.MAX_LOOP_ITER:
                raise ProgramFault('loop_overflow')
            with self._lock:
                self._loop_iter = iterations
            self._exec_body(body)
            iterations += 1

        with self._lock:
            self._loop_iter = None

    def _exec_loop_for(self, instr: dict) -> None:
        count = int(self._resolve(instr['count']))
        body  = instr['body']

        for i in range(count):
            self._vars['_loop_i'] = float(i)
            with self._lock:
                self._loop_iter = i
            self._exec_body(body)

        with self._lock:
            self._loop_iter = None

    def _exec_if(self, instr: dict) -> None:
        if self._eval_condition(instr['condition']):
            self._exec_body(instr['then'])
        elif 'else' in instr:
            self._exec_body(instr['else'])

    def _exec_call(self, instr: dict) -> None:
        sub_name = instr['sub']
        if sub_name not in self._subs:
            raise ProgramFault(f'program_error: unknown subroutine {sub_name!r}')
        self._call_depth += 1
        if self._call_depth > self.MAX_CALL_DEPTH:
            raise ProgramFault('call_depth')
        try:
            self._exec_body(self._subs[sub_name])
        except _ReturnSignal:
            pass
        finally:
            self._call_depth -= 1

    def _exec_jump(self, instr: dict) -> None:
        # JUMP/LABEL are difficult to implement cleanly in a recursive
        # interpreter. For v1, recommend using LOOP_WHILE / CALL instead.
        raise ProgramFault(
            'program_error: JUMP is not supported in interpreter v1; '
            'use LOOP_WHILE or CALL/RETURN for flow control')

    # ---- Variables --------------------------------------------------------

    def _exec_set_var(self, instr: dict) -> None:
        name = instr['name']
        if 'expr' in instr:
            self._vars[name] = self._eval_expr(instr['expr'])
        else:
            self._vars[name] = instr['value']

    def _exec_log(self, instr: dict) -> None:
        self._machine.log(self._expand(instr.get('message', '')))

    # ---- Helpers ----------------------------------------------------------

    def _check(self) -> None:
        """Pause/stop check — called between instructions and during waits."""
        while self._pause.is_set():
            if self._stop.is_set():
                raise ProgramFault('estop_triggered')
            time.sleep(0.05)
        if self._stop.is_set():
            raise ProgramFault('estop_triggered')

    def _resolve(self, value: Any) -> Any:
        """Resolve a $variable reference or return the literal value."""
        if isinstance(value, str) and value.startswith('$'):
            name = value[1:]
            if name not in self._vars:
                raise ProgramFault(f'program_error: undefined variable ${name}')
            return self._vars[name]
        return value

    def _expand(self, text: str) -> str:
        """Expand $variable references inside a string (for LOG messages).
        Whole-number floats (1.0, 2.0) are displayed as integers (1, 2).
        """
        def repl(m):
            name  = m.group(1)
            value = self._vars.get(name, f'${name}')
            if isinstance(value, float) and value == int(value):
                return str(int(value))
            return str(value)
        return re.sub(r'\$(\w+)', repl, str(text))

    def _eval_condition(self, condition: str) -> bool:
        """Evaluate a condition string to bool."""
        condition = condition.strip()

        if condition == 'true':  return True
        if condition == 'false': return False

        if condition.startswith('not '):
            return not self._eval_condition(condition[4:].strip())

        # Named sensor conditions
        if condition in ('material_present', 'pickup_ok', 'laser_safe', 'estop_hw'):
            return bool(self._machine.read_sensor(condition))

        # Variable comparison: $var op literal
        m = re.match(r'^\$(\w+)\s*(==|!=|<=|>=|<|>)\s*(.+)$', condition)
        if m:
            var_name, op, rhs_str = m.groups()
            if var_name not in self._vars:
                raise ProgramFault(f'program_error: undefined variable ${var_name}')
            lhs = self._vars[var_name]
            rhs_str = rhs_str.strip()
            if rhs_str == 'true':   rhs = True
            elif rhs_str == 'false': rhs = False
            else:
                try: rhs = float(rhs_str)
                except ValueError:
                    raise ProgramFault(
                        f'program_error: cannot parse condition rhs {rhs_str!r}')
            if op == '==': return lhs == rhs
            if op == '!=': return lhs != rhs
            if op == '<':  return lhs < rhs
            if op == '>':  return lhs > rhs
            if op == '<=': return lhs <= rhs
            if op == '>=': return lhs >= rhs

        raise ProgramFault(f'program_error: invalid condition {condition!r}')

    def _eval_expr(self, expr: str) -> float:
        """Evaluate a simple arithmetic expression with $variable substitution."""
        def repl(m):
            name = m.group(1)
            if name not in self._vars:
                raise ProgramFault(f'program_error: undefined variable ${name}')
            return str(float(self._vars[name]))
        resolved = re.sub(r'\$(\w+)', repl, expr)
        # Only allow safe arithmetic characters
        if not re.match(r'^[\d\s\.\+\-\*\/\(\)]+$', resolved):
            raise ProgramFault(f'program_error: unsafe expression {expr!r}')
        try:
            return float(eval(resolved, {"__builtins__": {}}, {}))
        except Exception as exc:
            raise ProgramFault(f'program_error: invalid expression {expr!r}: {exc}')
