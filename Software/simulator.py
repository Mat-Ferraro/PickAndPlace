#!/usr/bin/env python3
"""
Pick-and-Place Machine Simulator  v0.3
Implements communication-protocol.md v0.9 over TCP (localhost:9999).
Uses ProgramInterpreter from interpreter.py for program execution.

Console commands:
  load <path>        Load a job program JSON file
  run                Start executing the loaded program
  pause / resume     Pause or resume the running program
  fault <reason>     Inject a hardware fault
  estop              Trigger hardware e-stop
  estop_release      Release hardware e-stop
  laser_home/busy    Toggle laser head position
  material on/off    Toggle material presence
  status             Print machine state
  help / quit
"""

import base64
import json
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from interpreter import (
    MachineInterface, ProgramFault, ProgramInterpreter, ProgramValidator,
)


# ---------------------------------------------------------------------------
# States + matrices
# ---------------------------------------------------------------------------

class State(str, Enum):
    IDLE     = "IDLE"
    HOMING   = "HOMING"
    READY    = "READY"
    RUNNING  = "RUNNING"
    PAUSED   = "PAUSED"
    FAULTED  = "FAULTED"
    ESTOPPED    = "ESTOPPED"
    CALIBRATING = "CALIBRATING"


COMMAND_STATES: Dict[str, set] = {
    "home":           {State.IDLE, State.READY},
    "load_program":   {State.IDLE, State.READY},
    "run_program":    {State.READY},
    "pause":          {State.RUNNING},
    "resume":         {State.PAUSED},
    "reset_fault":    {State.FAULTED},
    "reset_estop":    {State.ESTOPPED},
    "jog":            {State.READY},
    "teach_position": {State.READY},
    "query_position": {State.IDLE, State.READY, State.FAULTED, State.ESTOPPED},
    "move_to":        {State.READY},
    "save_position":  {State.IDLE, State.READY},
    "set_param":      {State.IDLE, State.READY},
    "save_config":    {State.IDLE, State.READY},
    "load_config":    {State.IDLE, State.READY},
    "begin_transfer": {State.IDLE, State.READY},
    "program_chunk":  {State.IDLE, State.READY},
    "end_transfer":   {State.IDLE, State.READY},
    "query_sensors":  {State.IDLE, State.READY, State.RUNNING,
                       State.PAUSED, State.FAULTED, State.ESTOPPED},
    "set_output":     {State.IDLE, State.READY},
    "set_servo":      {State.IDLE, State.READY},
    "calibrate_axis":     {State.IDLE, State.READY},
    "set_cal_distance":   {State.CALIBRATING},
    "calibrate_sensors":  {State.IDLE, State.READY},
}

ALWAYS_ACCEPT = {
    "estop", "get_param", "query_status", "laser_safe",
    "query_positions", "get_program",
}

NAMED_POSITIONS = {"home", "laser_a", "laser_b", "deposit"}
VALID_OUTPUTS   = {"pump", "valve"}
VALID_SERVOS: Dict[str, set] = {
    "door":      {"open", "closed"},
    "laser_btn": {"press", "release"},
}
VALID_FAULTS_SET = {
    "pickup_lost", "pickup_failed", "sensor_timeout", "sensor_out_of_range",
    "homing_failed", "motion_fault", "laser_interlock", "config_invalid",
    "estop_triggered",
}


# ---------------------------------------------------------------------------
# Machine state
# ---------------------------------------------------------------------------

@dataclass
class MachineState:
    state: State                     = State.IDLE
    state_entered_at: float          = field(default_factory=time.monotonic)
    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    taught: Dict[str, Tuple[float, float, float]] = field(default_factory=lambda: {
        **{
            "home":    (0.0,   0.0,   0.0),
            "laser_a": (150.0, 80.0,  10.0),
            "laser_b": (200.0, 80.0,  10.0),
            "deposit": (50.0,  200.0, 5.0),
        },
        **_load_positions(),   # saved positions override defaults
    })
    tof_dist_mm: list = field(default_factory=lambda: [150, 150, 150, 150, 30, 25])
    tof_valid:   list = field(default_factory=lambda: [True] * 6)
    estop_hw:  bool = False
    start_btn: bool = False
    pause_btn: bool = False
    pump:            bool = False
    valve:           bool = False
    servo_door:      str  = "closed"
    servo_laser_btn: str  = "release"
    fault:           Optional[str] = None
    laser_head_home: bool          = True
    stored_program:  Optional[dict] = None
    home_surface_z:    float = 10.0
    deposit_surface_z: float =  5.0
    # Chunked transfer state
    xfer_buf:      bytes = field(default_factory=bytes)
    xfer_size:     int   = 0
    xfer_chunks:   int   = 0
    xfer_received: int   = 0
    # Stepper calibration
    cal_axis:      str   = ""
    cal_raw_steps: int   = 0
    steps_per_mm:  dict  = field(default_factory=lambda: {"X":0.0,"Y":0.0,"Z":0.0})
    tof_offsets:   list  = field(default_factory=lambda: [None, None, None, None])
    params: Dict[str, Any] = field(default_factory=lambda: {
        "laser_interlock_mode":        0,
        "status_rate_hz":              5,
        "servo_door_open_deg":         90,
        "servo_door_closed_deg":       0,
        "servo_laser_btn_press_deg":   45,
        "servo_laser_btn_release_deg": 0,
    })
    seq:          int   = 0
    uptime_start: float = field(default_factory=time.monotonic)
    interp_status: dict = field(default_factory=lambda: {
        'current_op': None, 'step_index': None,
        'loop_iter': None,  'variables': None,
    })

    def uptime_ms(self) -> int:
        return int((time.monotonic() - self.uptime_start) * 1000)

    def set_state(self, s: State) -> None:
        self.state = s
        self.state_entered_at = time.monotonic()

    @property
    def pickup_ok(self) -> bool:
        return all(self.tof_valid[i] and self.tof_dist_mm[i] < 200 for i in range(4))

    @property
    def material_present(self) -> bool:
        return self.tof_valid[5] and self.tof_dist_mm[5] < 50

    @property
    def laser_safe(self) -> bool:
        return self.laser_head_home

    def resolve_position_name(self) -> Optional[str]:
        for name, (x, y, z) in self.taught.items():
            if abs(self.x_mm - x) < 1.0 and abs(self.y_mm - y) < 1.0:
                return name
        return None


# ---------------------------------------------------------------------------
# Simulated machine
# ---------------------------------------------------------------------------

class SimulatedMachine(MachineInterface):
    MOVE_SPEED    = 25.0   # mm/s at 100%
    MIN_MOVE_S   = 1.5    # minimum seconds per move (makes sim transitions visible)

    def __init__(self, ms: MachineState, out_queue: queue.Queue):
        self._ms  = ms
        self._out = out_queue

    def get_position(self) -> tuple:
        return (self._ms.x_mm, self._ms.y_mm, self._ms.z_mm)

    def move_to(self, x, y, z, stop_event, speed_pct=80):
        ms    = self._ms
        speed = max(self.MOVE_SPEED * speed_pct / 100.0, 1.0)
        dist  = max(abs(x - ms.x_mm), abs(y - ms.y_mm), abs(z - ms.z_mm))
        dur   = max(dist / speed if dist > 0.1 else 0.1, self.MIN_MOVE_S)
        steps = max(int(dur / 0.05), 1)
        sx, sy, sz = ms.x_mm, ms.y_mm, ms.z_mm
        for i in range(1, steps + 1):
            if stop_event.is_set():
                raise ProgramFault('estop_triggered')
            t = i / steps
            ms.x_mm = sx + (x - sx) * t
            ms.y_mm = sy + (y - sy) * t
            ms.z_mm = sz + (z - sz) * t
            time.sleep(0.05)
        ms.x_mm, ms.y_mm, ms.z_mm = x, y, z

    def probe_z(self, x, y, approach_z, step_mm, max_depth_mm, threshold_mm, stop_event):
        self.move_to(x, y, approach_z, stop_event)
        ms = self._ms
        dep_x, dep_y, _ = ms.taught.get("deposit", (50.0, 200.0, 0.0))
        surface_z = ms.deposit_surface_z if (abs(x - dep_x) < 30 and abs(y - dep_y) < 30) else ms.home_surface_z
        current_z    = approach_z
        total_travel = 0.0
        while total_travel < max_depth_mm:
            if stop_event.is_set():
                raise ProgramFault('estop_triggered')
            current_z    -= step_mm
            total_travel += step_mm
            ms.z_mm       = current_z
            time.sleep(0.02)
            if max(current_z - surface_z, 0.0) < threshold_mm:
                return current_z
        raise ProgramFault('probe_failed')

    def home(self, axes, stop_event):
        tx = 0.0 if 'X' in axes else self._ms.x_mm
        ty = 0.0 if 'Y' in axes else self._ms.y_mm
        tz = 0.0 if 'Z' in axes else self._ms.z_mm
        self.move_to(tx, ty, tz, stop_event)

    def set_output(self, name, value):
        ms = self._ms
        if   name == 'pump':           ms.pump = bool(value)
        elif name == 'valve':          ms.valve = bool(value)
        elif name == 'servo_door':     ms.servo_door = str(value)
        elif name == 'servo_laser_btn': ms.servo_laser_btn = str(value)
        else: raise ProgramFault(f'program_error: unknown output {name!r}')

    def read_sensor(self, sensor):
        ms = self._ms
        if sensor == 'material_present': return ms.material_present
        if sensor == 'pickup_ok':        return ms.pickup_ok
        if sensor == 'laser_safe':       return ms.laser_safe
        if sensor == 'estop_hw':         return ms.estop_hw
        import re
        m = re.match(r'^tof_ch(\d)_mm$', sensor)
        if m:
            ch = int(m.group(1))
            if 0 <= ch <= 5:
                return float(ms.tof_dist_mm[ch])
        raise ProgramFault(f'program_error: unknown sensor {sensor!r}')

    def delay(self, ms_duration, stop_event):
        deadline = time.monotonic() + ms_duration / 1000.0
        while time.monotonic() < deadline:
            if stop_event.is_set():
                raise ProgramFault('estop_triggered')
            time.sleep(0.02)

    def log(self, message):
        self._out.put(json.dumps({"type": "log", "message": message}))


POSITIONS_FILE = "pnp_positions.json"


def _load_positions() -> dict:
    """Load saved named positions from disk, falling back to defaults."""
    try:
        with open(POSITIONS_FILE) as f:
            raw = json.load(f)
        # JSON stores lists [x,y,z]; convert to tuples
        return {k: tuple(v) for k, v in raw.items()}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {}


def _save_positions(taught: dict) -> None:
    """Persist named positions to disk as JSON."""
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump({k: list(v) for k, v in taught.items()}, f, indent=2)
    except OSError as e:
        print(f"[sim] Warning: could not save positions: {e}")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class StateMachine:
    def __init__(self):
        self.ms          = MachineState()
        self.cmd_queue:  queue.Queue = queue.Queue()
        self.out_queue:  queue.Queue = queue.Queue()
        self._lock       = threading.Lock()
        self._validator  = ProgramValidator()
        self._interp:    Optional[ProgramInterpreter] = None
        self._interp_thread: Optional[threading.Thread] = None
        self._interp_result: queue.Queue = queue.Queue()
        self._pause_event = threading.Event()
        self._stop_event  = threading.Event()
        self._homing_done_at: Optional[float] = None
        self._cal_done_at:    Optional[float] = None

    # ---- External API -----------------------------------------------------

    def enqueue_command(self, msg):    self.cmd_queue.put(msg)
    def enqueue_fault(self, reason):   self.cmd_queue.put({"_internal":"fault","reason":reason})
    def enqueue_estop(self, released): self.cmd_queue.put({"_internal":"estop_hw","released":released})
    def enqueue_laser_state(self, home): self.cmd_queue.put({"_internal":"laser_state","home":home})
    def enqueue_material_state(self, present): self.cmd_queue.put({"_internal":"material_state","present":present})
    def enqueue_button(self, button): self.cmd_queue.put({"_internal":"button","button":button})
    def enqueue_jam(self, axis): self.cmd_queue.put({"_internal":"jam","axis":axis})
    def send(self, msg): self.out_queue.put(json.dumps(msg))
    def build_status(self):
        with self._lock: return self._build_status()
    def get_status_rate(self):
        return float(self.ms.params.get("status_rate_hz", 5))

    # ---- Tick -------------------------------------------------------------

    def tick(self):
        with self._lock:
            self._drain_queue()
            self._tick_homing()
            self._tick_calibrating()
            self._tick_interpreter()

    def _tick_homing(self):
        if (self.ms.state == State.HOMING and self._homing_done_at and
                time.monotonic() >= self._homing_done_at):
            self.ms.x_mm = self.ms.y_mm = self.ms.z_mm = 0.0
            self.ms.set_state(State.READY)
            self._homing_done_at = None

    def _tick_calibrating(self):
        if (self.ms.state == State.CALIBRATING and self._cal_done_at and
                time.monotonic() >= self._cal_done_at):
            # Simulate a traverse: fake steps based on axis
            fake_steps = {"X": 12800, "Y": 12800, "Z": 6400}
            self.ms.cal_raw_steps = fake_steps.get(self.ms.cal_axis, 3200)
            self._cal_done_at = None   # stay CALIBRATING, await set_cal_distance

    def _tick_interpreter(self):
        if self._interp:
            self.ms.interp_status = self._interp.status
        try:
            outcome, data = self._interp_result.get_nowait()
        except queue.Empty:
            return
        if outcome == 'halt':
            # Only transition to READY if not already in a terminal state
            if self.ms.state == State.RUNNING:
                self.ms.set_state(State.READY)
        else:
            # Don't overwrite an already-terminal fault state — E-stop or a
            # motion_fault (jam) that aborted the run keeps its own reason.
            if self.ms.state not in (State.ESTOPPED, State.FAULTED):
                self.ms.fault = data
                self.ms.set_state(State.FAULTED)
                self.send({"type": "fault", "reason": data})
        self._interp = self._interp_thread = None
        self.ms.interp_status = {'current_op':None,'step_index':None,
                                  'loop_iter':None,'variables':None}

    # ---- Queue drain ------------------------------------------------------

    def _drain_queue(self):
        while not self.cmd_queue.empty():
            try:
                msg = self.cmd_queue.get_nowait()
            except queue.Empty:
                break
            if "_internal" in msg:
                self._handle_internal(msg)
            else:
                self._handle_command(msg)

    def _handle_internal(self, msg):
        ms, kind = self.ms, msg["_internal"]
        if kind == "fault":
            ms.fault = msg["reason"]
            ms.set_state(State.FAULTED)
            self.send({"type":"fault","reason":msg["reason"]})
        elif kind == "estop_hw":
            if msg["released"]:
                ms.estop_hw = False
                # Releasing the latched E-stop returns the machine to IDLE
                # (not READY — position trust is lost, so a re-home is needed).
                if ms.state == State.ESTOPPED:
                    self._stop_event.clear()
                    ms.fault = None
                    ms.set_state(State.IDLE)
            else:
                ms.estop_hw = True
                self._stop_event.set()
                ms.fault = "estop_triggered"
                ms.set_state(State.ESTOPPED)
                self.send({"type":"fault","reason":"estop_triggered"})
        elif kind == "laser_state":
            ms.laser_head_home = msg["home"]
            ms.tof_dist_mm[4]  = 30 if msg["home"] else 400
        elif kind == "material_state":
            ms.tof_dist_mm[5] = 25 if msg["present"] else 300
            ms.tof_valid[5]   = True
        elif kind == "jam":
            # StallGuard-style stall/jam: abort any motion, raise motion_fault.
            if ms.state != State.ESTOPPED:
                self._stop_event.set()
                ms.fault = "motion_fault"
                ms.set_state(State.FAULTED)
                self.send({"type":"fault","reason":"motion_fault","axis":msg.get("axis")})
        elif kind == "button":
            self._handle_button(msg["button"])

    def _handle_button(self, button):
        """Emulate a physical button press. Drives the same state transition the
        firmware's button handler would, but sends NO command ack — an attached
        GUI learns of the change from the next status broadcast, exactly as with
        real hardware. See architecture.md §9.1 for the button map."""
        ms, s = self.ms, self.ms.state
        if button == "start":            # "proceed"
            if s == State.IDLE:
                ms.set_state(State.HOMING)
                self._homing_done_at = time.monotonic() + 3.0
            elif s == State.READY:
                if ms.stored_program is not None:   # else refused (would beep)
                    self._start_interpreter()
                    ms.set_state(State.RUNNING)
            elif s == State.PAUSED:
                self._pause_event.clear()
                ms.set_state(State.RUNNING)
        elif button == "pause":          # "halt / dismiss"
            if s == State.RUNNING:
                self._pause_event.set()
                ms.set_state(State.PAUSED)
            elif s == State.FAULTED:
                self._stop_event.clear()
                ms.fault = None
                ms.set_state(State.IDLE)

    def _handle_command(self, msg):
        ms = self.ms
        cmd_id = msg.get("id")
        cmd    = msg.get("cmd")
        if cmd_id is None:
            self.send({"type":"nack","id":None,"cmd":cmd,"reason":"missing_id"}); return
        if cmd is None:
            self.send({"type":"nack","id":cmd_id,"cmd":None,"reason":"malformed"}); return
        if cmd not in ALWAYS_ACCEPT and cmd not in COMMAND_STATES:
            self.send({"type":"nack","id":cmd_id,"cmd":cmd,"reason":"unknown_cmd"}); return
        if cmd not in ALWAYS_ACCEPT:
            if ms.state not in COMMAND_STATES.get(cmd, set()):
                reason = ("estop_active"  if ms.state == State.ESTOPPED   else
                          "hw_fault"      if ms.state == State.FAULTED    else
                          "calibrating"   if ms.state == State.CALIBRATING else "not_ready")
                self.send({"type":"nack","id":cmd_id,"cmd":cmd,"reason":reason}); return
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler:
            handler(cmd_id, msg)
        else:
            self.send({"type":"ack","id":cmd_id,"cmd":cmd})

    # ---- Command handlers -------------------------------------------------

    def _cmd_home(self, i, m):
        self.ms.set_state(State.HOMING)
        self._homing_done_at = time.monotonic() + 3.0
        self.send({"type":"ack","id":i,"cmd":"home"})

    def _cmd_load_program(self, i, m):
        prog = m.get("program")
        if not isinstance(prog, dict):
            self.send({"type":"nack","id":i,"cmd":"load_program",
                       "reason":"invalid_param","detail":"Missing 'program' object"}); return
        ok, err = self._validator.validate(prog)
        if not ok:
            self.send({"type":"nack","id":i,"cmd":"load_program",
                       "reason":"invalid_param","detail":err}); return
        self.ms.stored_program = prog
        self.send({"type":"ack","id":i,"cmd":"load_program",
                   "instructions":len(prog.get('program',[])),
                   "bytes":len(json.dumps(prog))})

    def _cmd_get_program(self, i, m):
        if self.ms.stored_program is None:
            self.send({"type":"nack","id":i,"cmd":"get_program","reason":"no_program"})
        else:
            self.send({"type":"ack","id":i,"cmd":"get_program","program":self.ms.stored_program})

    def _cmd_run_program(self, i, m):
        if self.ms.stored_program is None:
            self.send({"type":"nack","id":i,"cmd":"run_program","reason":"no_program"}); return
        self._start_interpreter()
        self.ms.set_state(State.RUNNING)
        self.send({"type":"ack","id":i,"cmd":"run_program"})

    def _cmd_pause(self, i, m):
        self._pause_event.set()
        self.ms.set_state(State.PAUSED)
        self.send({"type":"ack","id":i,"cmd":"pause"})

    def _cmd_resume(self, i, m):
        self._pause_event.clear()
        self.ms.set_state(State.RUNNING)
        self.send({"type":"ack","id":i,"cmd":"resume"})

    def _cmd_estop(self, i, m):
        self._stop_event.set()
        self.ms.fault = "estop_triggered"
        self.ms.set_state(State.ESTOPPED)
        self.send({"type":"ack","id":i,"cmd":"estop"})
        self.send({"type":"fault","reason":"estop_triggered"})

    def _cmd_reset_fault(self, i, m):
        self._stop_event.clear()
        self.ms.fault = None
        self.ms.set_state(State.IDLE)
        self.send({"type":"ack","id":i,"cmd":"reset_fault"})

    def _cmd_reset_estop(self, i, m):
        if self.ms.estop_hw:
            self.send({"type":"nack","id":i,"cmd":"reset_estop","reason":"hw_fault"}); return
        self._stop_event.clear()
        self.ms.fault = None
        self.ms.set_state(State.IDLE)
        self.send({"type":"ack","id":i,"cmd":"reset_estop"})

    def _cmd_jog(self, i, m):
        axis = str(m.get("axis","")).upper()
        dist = m.get("distance_mm", 0.0) * m.get("dir", 1)
        if axis not in ("X","Y","Z"):
            self.send({"type":"nack","id":i,"cmd":"jog","reason":"invalid_param"}); return
        if axis == "X": self.ms.x_mm += dist
        elif axis == "Y": self.ms.y_mm += dist
        elif axis == "Z": self.ms.z_mm += dist
        self.send({"type":"ack","id":i,"cmd":"jog"})

    def _cmd_teach_position(self, i, m):
        name = m.get("name","")
        if name not in NAMED_POSITIONS:
            self.send({"type":"nack","id":i,"cmd":"teach_position","reason":"invalid_param"}); return
        self.ms.taught[name] = (self.ms.x_mm, self.ms.y_mm, self.ms.z_mm)
        _save_positions(self.ms.taught)
        self.send({"type":"ack","id":i,"cmd":"teach_position"})

    def _cmd_query_position(self, i, m):
        ms = self.ms
        self.send({"type":"ack","id":i,"cmd":"query_position",
                   "x_mm":round(ms.x_mm,2),"y_mm":round(ms.y_mm,2),
                   "z_mm":round(ms.z_mm,2),"position_name":ms.resolve_position_name()})

    def _cmd_move_to(self, i, m):
        x,y,z = m.get("x_mm"), m.get("y_mm"), m.get("z_mm")
        if None in (x,y,z):
            self.send({"type":"nack","id":i,"cmd":"move_to","reason":"invalid_param"}); return
        self.ms.x_mm,self.ms.y_mm,self.ms.z_mm = float(x),float(y),float(z)
        self.send({"type":"ack","id":i,"cmd":"move_to"})

    def _cmd_save_position(self, i, m):
        name,x,y,z = m.get("name",""),m.get("x_mm"),m.get("y_mm"),m.get("z_mm")
        if name not in NAMED_POSITIONS or None in (x,y,z):
            self.send({"type":"nack","id":i,"cmd":"save_position","reason":"invalid_param"}); return
        self.ms.taught[name] = (float(x),float(y),float(z))
        _save_positions(self.ms.taught)
        self.send({"type":"ack","id":i,"cmd":"save_position"})

    def _cmd_set_param(self, i, m):
        k,v = m.get("key"), m.get("value")
        if k not in self.ms.params:
            self.send({"type":"nack","id":i,"cmd":"set_param","reason":"invalid_param"}); return
        self.ms.params[k] = v
        self.send({"type":"ack","id":i,"cmd":"set_param"})

    def _cmd_get_param(self, i, m):
        k = m.get("key")
        # Calibration params are stored separately from the general params dict.
        cal_map = {
            "steps_per_mm_x": "X",
            "steps_per_mm_y": "Y",
            "steps_per_mm_z": "Z",
        }
        if k in cal_map:
            axis = cal_map[k]
            val  = self.ms.steps_per_mm.get(axis, 0.0)
            self.send({"type":"ack","id":i,"cmd":"get_param","key":k,"value":val}); return
        # ToF offset params
        if k and k.startswith("tof_offset_"):
            try:
                ch  = int(k.split("_")[-1])
                val = self.ms.tof_offsets[ch]
                self.send({"type":"ack","id":i,"cmd":"get_param","key":k,"value":val}); return
            except (IndexError, ValueError):
                pass
        if k not in self.ms.params:
            self.send({"type":"nack","id":i,"cmd":"get_param","reason":"invalid_param"}); return
        self.send({"type":"ack","id":i,"cmd":"get_param","key":k,"value":self.ms.params[k]})

    def _cmd_begin_transfer(self, i, m):
        size   = int(m.get("size",   0))
        chunks = int(m.get("chunks", 0))
        if size <= 0 or chunks <= 0:
            self.send({"type":"nack","id":i,"cmd":"begin_transfer","reason":"invalid_param"}); return
        self.ms.xfer_buf = b""; self.ms.xfer_size = size
        self.ms.xfer_chunks = chunks; self.ms.xfer_received = 0
        self.send({"type":"ack","id":i,"cmd":"begin_transfer"})

    def _cmd_program_chunk(self, i, m):
        idx = m.get("index", -1)
        if self.ms.xfer_size == 0:
            self.send({"type":"nack","id":i,"cmd":"program_chunk","reason":"no_transfer_in_progress"}); return
        if idx != self.ms.xfer_received:
            self.send({"type":"nack","id":i,"cmd":"program_chunk",
                       "reason":f"out_of_order_expected_{self.ms.xfer_received}"}); return
        try:
            self.ms.xfer_buf += base64.b64decode(m.get("data",""))
        except Exception:
            self.send({"type":"nack","id":i,"cmd":"program_chunk","reason":"bad_base64"}); return
        self.ms.xfer_received += 1
        self.send({"type":"ack","id":i,"cmd":"program_chunk","index":idx})

    def _cmd_end_transfer(self, i, m):
        if self.ms.xfer_size == 0:
            self.send({"type":"nack","id":i,"cmd":"end_transfer","reason":"no_transfer_in_progress"}); return
        if self.ms.xfer_received != self.ms.xfer_chunks:
            reason = f"incomplete_{self.ms.xfer_received}_of_{self.ms.xfer_chunks}"
            self.send({"type":"nack","id":i,"cmd":"end_transfer","reason":reason})
            self.ms.xfer_buf = b""; self.ms.xfer_size = 0; return
        if len(self.ms.xfer_buf) != self.ms.xfer_size:
            self.send({"type":"nack","id":i,"cmd":"end_transfer","reason":"size_mismatch"})
            self.ms.xfer_buf = b""; self.ms.xfer_size = 0; return
        try:
            program = json.loads(self.ms.xfer_buf.decode())
        except json.JSONDecodeError as e:
            self.send({"type":"nack","id":i,"cmd":"end_transfer","reason":f"json_error:{e}"})
            self.ms.xfer_buf = b""; self.ms.xfer_size = 0; return
        self.ms.xfer_buf = b""; self.ms.xfer_size = 0
        self._cmd_load_program(i, {"cmd":"load_program","program":program})

    def _cmd_save_config(self, i, m):
        _save_positions(self.ms.taught)
        self.send({"type":"ack","id":i,"cmd":"save_config"})

    def _cmd_load_config(self, i, m):
        self.send({"type":"ack","id":i,"cmd":"load_config"})

    def _cmd_query_status(self, i, m):
        self.send(self._build_status())

    def _cmd_query_positions(self, i, m):
        self.send({"type":"ack","id":i,"cmd":"query_positions",
                   "positions":{n:{"x_mm":round(x,2),"y_mm":round(y,2),"z_mm":round(z,2)}
                                for n,(x,y,z) in self.ms.taught.items()}})

    def _cmd_query_sensors(self, i, m):
        ms = self.ms
        self.send({"type":"ack","id":i,"cmd":"query_sensors",
                   "tof":[{"ch":c,"dist_mm":ms.tof_dist_mm[c],"valid":ms.tof_valid[c]} for c in range(6)],
                   "outputs":{"pump":ms.pump,"valve":ms.valve,
                               "servo_door":ms.servo_door,"servo_laser_btn":ms.servo_laser_btn},
                   "inputs":{"estop_hw":ms.estop_hw,"start_btn":ms.start_btn,"pause_btn":ms.pause_btn}})

    def _cmd_calibrate_sensors(self, i, m):
        """Read ch0-ch3 and store as the blocked/touching baseline offsets."""
        offsets = [self.ms.tof_dist_mm[ch] for ch in range(4)]
        self.ms.tof_offsets = list(offsets)
        self.send({"type":"ack","id":i,"cmd":"calibrate_sensors",
                   "offsets":offsets})

    def _cmd_calibrate_axis(self, i, m):
        axis = str(m.get("axis", "X")).upper()
        if axis not in ("X", "Y", "Z"):
            self.send({"type":"nack","id":i,"cmd":"calibrate_axis","reason":"invalid_axis"}); return
        self.ms.cal_axis = axis
        self.ms.cal_raw_steps = 0
        self.ms.set_state(State.CALIBRATING)
        self._cal_done_at = time.monotonic() + 2.0   # 2s simulated traverse
        self.send({"type":"ack","id":i,"cmd":"calibrate_axis"})

    def _cmd_set_cal_distance(self, i, m):
        if self.ms.cal_raw_steps == 0:
            self.send({"type":"nack","id":i,"cmd":"set_cal_distance","reason":"traverse_not_done"}); return
        dist = float(m.get("mm", 0))
        if dist <= 0:
            self.send({"type":"nack","id":i,"cmd":"set_cal_distance","reason":"invalid_distance"}); return
        axis = self.ms.cal_axis or str(m.get("axis","X")).upper()
        self.ms.steps_per_mm[axis] = self.ms.cal_raw_steps / dist
        self.ms.cal_raw_steps = 0
        self.ms.cal_axis = ""
        self.ms.set_state(State.IDLE)
        self.send({"type":"ack","id":i,"cmd":"set_cal_distance"})

    def _cmd_set_output(self, i, m):
        out,state = m.get("output",""), m.get("state")
        if out not in VALID_OUTPUTS or not isinstance(state, bool):
            self.send({"type":"nack","id":i,"cmd":"set_output","reason":"invalid_param"}); return
        setattr(self.ms, out, state)
        self.send({"type":"ack","id":i,"cmd":"set_output"})

    def _cmd_set_servo(self, i, m):
        servo,pos = m.get("servo",""), m.get("position","")
        if servo not in VALID_SERVOS or pos not in VALID_SERVOS.get(servo,set()):
            self.send({"type":"nack","id":i,"cmd":"set_servo","reason":"invalid_param"}); return
        setattr(self.ms, f"servo_{servo}", pos)
        self.send({"type":"ack","id":i,"cmd":"set_servo"})

    # ---- Interpreter management -------------------------------------------

    def _start_interpreter(self):
        ms      = self.ms
        machine = SimulatedMachine(ms, self.out_queue)
        self._pause_event.clear()
        self._stop_event.clear()
        rq = self._interp_result
        interp = ProgramInterpreter(ms.stored_program, machine,
                                     self._pause_event, self._stop_event)
        self._interp = interp
        def _run():
            try:
                interp.run()
                rq.put(('halt', None))
            except ProgramFault as e:
                rq.put(('fault', e.reason))
            except Exception as e:
                rq.put(('fault', f'program_error: {e}'))
        self._interp_thread = threading.Thread(target=_run, daemon=True)
        self._interp_thread.start()

    # ---- Status -----------------------------------------------------------

    def _build_status(self):
        ms = self.ms
        ms.seq += 1
        ist = ms.interp_status
        return {
            "type": "status", "seq": ms.seq, "uptime_ms": ms.uptime_ms(),
            "state": ms.state.value, "position_name": ms.resolve_position_name(),
            "x_mm": round(ms.x_mm,2), "y_mm": round(ms.y_mm,2), "z_mm": round(ms.z_mm,2),
            "pickup_ok": ms.pickup_ok, "material_present": ms.material_present,
            "laser_safe": ms.laser_safe, "estop_hw": ms.estop_hw,
            "program_loaded": ms.stored_program is not None, "fault": ms.fault,
            "current_op": ist.get("current_op"), "step_index": ist.get("step_index"),
            "loop_iter":  ist.get("loop_iter"),  "variables":  ist.get("variables"),
            "cal_axis":  ms.cal_axis   if ms.cal_raw_steps > 0 else None,
            "cal_steps": ms.cal_raw_steps if ms.cal_raw_steps > 0 else None,
        }


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------

HOST, PORT = "localhost", 9999

def _client_reader(conn, sm, stop):
    buf = b""
    conn.settimeout(0.1)
    while not stop.is_set():
        try:
            chunk = conn.recv(1024)
            if not chunk: break
            buf += chunk
        except socket.timeout: continue
        except OSError: break
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line: continue
            # load_program carries a full JSON payload — allow up to 64 KB.
            # Real firmware uses chunked transfer; the simulator accepts it whole.
            MAX_LINE    = 65536
            SMALL_LIMIT = 256
            if len(line) > MAX_LINE:
                sm.send({"type":"nack","id":None,"cmd":None,"reason":"oversized"}); continue
            if len(line) > SMALL_LIMIT:
                try:
                    parsed = json.loads(line)
                    cid = parsed.get("id")
                    cmd = parsed.get("cmd")
                    if cmd == "load_program":
                        sm.enqueue_command(parsed)
                        continue
                except Exception:
                    cid = cmd = None
                sm.send({"type":"nack","id":cid,"cmd":cmd,"reason":"oversized"}); continue
            try: sm.enqueue_command(json.loads(line))
            except json.JSONDecodeError:
                sm.send({"type":"nack","id":None,"cmd":None,"reason":"malformed"})

def _client_writer(conn, sm, stop):
    while not stop.is_set():
        try:
            line = sm.out_queue.get(timeout=0.05)
            conn.sendall((line+"\n").encode())
        except queue.Empty: continue
        except OSError: break

def run_server(sm, stop):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT)); srv.listen(1); srv.settimeout(0.5)
    print(f"[sim] Listening on {HOST}:{PORT}")
    while not stop.is_set():
        try: conn, addr = srv.accept()
        except socket.timeout: continue
        print(f"[sim] Client connected: {addr}")
        cs = threading.Event()
        r = threading.Thread(target=_client_reader, args=(conn,sm,cs), daemon=True)
        w = threading.Thread(target=_client_writer, args=(conn,sm,cs), daemon=True)
        r.start(); w.start(); r.join(); cs.set(); w.join(); conn.close()
        print("[sim] Client disconnected")
    srv.close()

def status_broadcaster(sm, stop):
    while not stop.is_set():
        rate = sm.get_status_rate()
        sm.send(sm.build_status())
        time.sleep(1.0 / max(rate, 0.1))


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

HELP_TEXT = """
Console commands:
  load <path>        Load a job program JSON file
  run                Start the loaded program
  pause / resume     Pause or resume
  press start|pause  Emulate a PHYSICAL button press (drives state, no ack;
                     an attached GUI updates from the next status broadcast)
  fault <reason>     Inject a hardware fault
  jam [axis]         Inject a StallGuard stall/jam -> motion_fault (default X)
  estop              Trigger hardware e-stop (latched)
  estop_release      Release the latched e-stop (returns to IDLE)
  laser_home/busy    Toggle laser head position
  material on/off    Toggle material presence (TOF-6)
  surface_home <z>   Set virtual stack surface Z (default 10.0mm)
  surface_deposit <z> Set virtual deposit surface Z (default 5.0mm)
  status             Print machine state
  help / quit
"""

def console_loop(sm, stop):
    print(HELP_TEXT)
    while not stop.is_set():
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            stop.set(); break
        if not line: continue
        parts = line.split(); cmd = parts[0].lower()

        if cmd == "quit":
            stop.set()
        elif cmd == "load":
            if len(parts) < 2: print("Usage: load <path>"); continue
            try:
                with open(parts[1]) as f: prog = json.load(f)
                ok, err = sm._validator.validate(prog)
                if ok:
                    sm.ms.stored_program = prog
                    print(f"[sim] Loaded: {prog.get('name','?')} ({len(prog.get('program',[]))} instructions)")
                else:
                    print(f"[sim] Validation error: {err}")
            except Exception as e:
                print(f"[sim] Error: {e}")
        elif cmd == "run":
            if sm.ms.stored_program is None: print("[sim] No program loaded"); continue
            if sm.ms.state != State.READY: print(f"[sim] Not READY (current: {sm.ms.state.value})"); continue
            with sm._lock:
                sm._start_interpreter()
                sm.ms.set_state(State.RUNNING)
            print("[sim] Program started")
        elif cmd == "pause":
            sm._pause_event.set(); sm.ms.set_state(State.PAUSED); print("[sim] Paused")
        elif cmd == "resume":
            sm._pause_event.clear(); sm.ms.set_state(State.RUNNING); print("[sim] Resumed")
        elif cmd == "press":
            if len(parts) < 2 or parts[1] not in ("start", "pause"):
                print("Usage: press start | pause"); continue
            sm.enqueue_button(parts[1]); print(f"[sim] Physical button: {parts[1]}")
        elif cmd == "jam":
            axis = parts[1].upper() if len(parts) > 1 else "X"
            sm.enqueue_jam(axis); print(f"[sim] Jam on {axis} -> motion_fault")
        elif cmd == "fault":
            if len(parts) < 2: print("Usage: fault <reason>"); continue
            if parts[1] not in VALID_FAULTS_SET:
                print(f"Valid faults: {', '.join(sorted(VALID_FAULTS_SET))}"); continue
            sm.enqueue_fault(parts[1]); print(f"[sim] Fault: {parts[1]}")
        elif cmd == "estop":
            sm.enqueue_estop(released=False); print("[sim] E-stop triggered")
        elif cmd == "estop_release":
            sm.enqueue_estop(released=True); print("[sim] E-stop released")
        elif cmd == "laser_home":
            sm.enqueue_laser_state(home=True); print("[sim] Laser -> home")
        elif cmd == "laser_busy":
            sm.enqueue_laser_state(home=False); print("[sim] Laser -> busy")
        elif cmd == "material":
            if len(parts) < 2 or parts[1] not in ("on","off"):
                print("Usage: material on | off"); continue
            present = parts[1] == "on"
            sm.enqueue_material_state(present)
            print(f"[sim] Material -> {'present' if present else 'absent'}")

        elif cmd == "surface_home":
            if len(parts) < 2:
                print(f"Home surface Z: {sm.ms.home_surface_z:.1f}mm  (usage: surface_home <z>)")
            else:
                sm.ms.home_surface_z = float(parts[1])
                print(f"[sim] Home surface Z -> {sm.ms.home_surface_z:.1f}mm")

        elif cmd == "surface_deposit":
            if len(parts) < 2:
                print(f"Deposit surface Z: {sm.ms.deposit_surface_z:.1f}mm  (usage: surface_deposit <z>)")
            else:
                sm.ms.deposit_surface_z = float(parts[1])
                print(f"[sim] Deposit surface Z -> {sm.ms.deposit_surface_z:.1f}mm")

        elif cmd == "status":
            ms = sm.ms
            print(f"  state    : {ms.state.value}")
            print(f"  position : x={ms.x_mm:.2f}  y={ms.y_mm:.2f}  z={ms.z_mm:.2f}")
            print(f"  at       : {ms.resolve_position_name()}")
            print(f"  material : {ms.material_present}  laser_safe: {ms.laser_safe}")
            print(f"  estop_hw : {ms.estop_hw}  fault: {ms.fault}")
            print(f"  program  : {'loaded - ' + ms.stored_program.get('name','?') if ms.stored_program else 'none'}")
            if ms.interp_status.get('current_op'):
                ist = ms.interp_status
                print(f"  op       : {ist['current_op']}  step={ist['step_index']}  loop={ist['loop_iter']}")
                print(f"  vars     : {ist['variables']}")
        elif cmd == "help":
            print(HELP_TEXT)
        else:
            print(f"Unknown: '{cmd}'  (type 'help')")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    sm   = StateMachine()
    stop = threading.Event()
    def tick_loop():
        while not stop.is_set():
            sm.tick(); time.sleep(0.05)
    for t in [
        threading.Thread(target=run_server,        args=(sm,stop), daemon=True),
        threading.Thread(target=status_broadcaster, args=(sm,stop), daemon=True),
        threading.Thread(target=tick_loop,                          daemon=True),
    ]: t.start()
    console_loop(sm, stop)
    print("[sim] Shutting down..."); stop.set()

if __name__ == "__main__":
    main()
