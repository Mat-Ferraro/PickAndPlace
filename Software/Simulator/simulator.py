#!/usr/bin/env python3
"""
Pick-and-Place Machine Simulator v0.1
Implements communication-protocol.md over TCP (localhost:9999).

GUI connection:  pyserial  socket://localhost:9999/
Quick test:      telnet localhost 9999  (then type JSON lines)

Console commands (type in the simulator terminal while it runs):
  fault <reason>     Inject a fault  (e.g.  fault pickup_lost)
  estop              Trigger hardware e-stop
  estop_release      Release hardware e-stop
  laser_home         Set laser head to home position (TOF-5 clear)
  laser_busy         Set laser head to away/busy position
  status             Print current machine state to console
  help               Show this help
  quit               Shut down simulator
"""

import json
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class State(str, Enum):
    IDLE                   = "IDLE"
    HOMING                 = "HOMING"
    READY                  = "READY"
    PICKING                = "PICKING"
    VERIFY_PICKUP          = "VERIFY_PICKUP"
    MOVING_TO_LASER        = "MOVING_TO_LASER"
    WAITING_FOR_LASER_SAFE = "WAITING_FOR_LASER_SAFE"
    PLACING                = "PLACING"
    WAITING_FOR_CUT        = "WAITING_FOR_CUT"
    RETRIEVING             = "RETRIEVING"
    DEPOSITING             = "DEPOSITING"
    PAUSED                 = "PAUSED"
    FAULTED                = "FAULTED"
    ESTOPPED               = "ESTOPPED"


# Automatic timed transitions: state -> (duration_sec, next_state)
# None as next_state means "handle specially" (job loop / done).
# States absent from this dict require an explicit condition to advance.
TIMED_TRANSITIONS: Dict[State, Tuple[float, Optional[State]]] = {
    State.HOMING:          (3.0, State.READY),
    State.PICKING:         (2.0, State.VERIFY_PICKUP),
    State.VERIFY_PICKUP:   (1.0, State.MOVING_TO_LASER),
    State.MOVING_TO_LASER: (3.0, State.WAITING_FOR_LASER_SAFE),
    State.PLACING:         (1.5, State.WAITING_FOR_CUT),
    State.WAITING_FOR_CUT: (5.0, State.RETRIEVING),
    State.RETRIEVING:      (3.0, State.DEPOSITING),
    State.DEPOSITING:      (2.0, None),   # handled in _tick_transitions
}

# State x command matrix.
# Commands in ALWAYS_ACCEPT bypass this check entirely.
COMMAND_STATES: Dict[str, set] = {
    "home":           {State.IDLE, State.READY},
    "start_job":      {State.READY},
    "pause":          {State.PICKING, State.VERIFY_PICKUP, State.MOVING_TO_LASER,
                       State.WAITING_FOR_LASER_SAFE, State.PLACING,
                       State.WAITING_FOR_CUT, State.RETRIEVING, State.DEPOSITING},
    "resume":         {State.PAUSED},
    "reset_fault":    {State.FAULTED},
    "reset_estop":    {State.ESTOPPED},
    "jog":            {State.READY},
    "teach_position": {State.READY},
    "query_position": {State.IDLE, State.READY, State.PAUSED, State.FAULTED, State.ESTOPPED},
    "set_param":      {State.IDLE, State.READY},
    "save_config":    {State.IDLE, State.READY},
    "load_config":    {State.IDLE, State.READY},
    "query_sensors":  {State.IDLE, State.READY, State.PAUSED, State.FAULTED, State.ESTOPPED},
    "set_output":     {State.IDLE, State.READY},
    "set_servo":      {State.IDLE, State.READY},
    "move_to":        {State.READY},
    "save_position":  {State.IDLE, State.READY},
}

ALWAYS_ACCEPT = {"estop", "get_param", "query_status", "laser_safe", "query_positions"}

NAMED_POSITIONS = {"home", "laser_a", "laser_b", "deposit"}
VALID_OUTPUTS   = {"pump", "valve"}
VALID_SERVOS: Dict[str, set] = {
    "door":      {"open", "closed"},
    "laser_btn": {"press", "release"},
}
VALID_FAULTS    = {
    "pickup_lost", "pickup_failed", "sensor_timeout", "sensor_out_of_range",
    "homing_failed", "motion_fault", "laser_interlock", "config_invalid",
    "estop_triggered",
}


# ---------------------------------------------------------------------------
# Machine state (all mutable data in one place)
# ---------------------------------------------------------------------------

@dataclass
class MachineState:
    # State machine
    state: State                    = State.IDLE
    pre_pause_state: Optional[State] = None
    state_entered_at: float         = field(default_factory=time.monotonic)

    # Job tracking
    job_count: int = 0
    job_total: int = 0

    # Simulated position
    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0

    # Taught named positions  {name: (x, y, z)}
    taught: Dict[str, Tuple[float, float, float]] = field(default_factory=lambda: {
        "home":    (0.0,   0.0,   0.0),
        "laser_a": (150.0, 80.0,  10.0),
        "laser_b": (200.0, 80.0,  10.0),
        "deposit": (50.0,  200.0, 5.0),
    })

    # ToF sensors  [ch0..ch3 = pickup, ch4 = laser-head home, ch5 = material]
    tof_dist_mm: list = field(default_factory=lambda: [150, 150, 150, 150, 30, 25])
    tof_valid:   list = field(default_factory=lambda: [True] * 6)

    # Physical inputs
    estop_hw:  bool = False
    start_btn: bool = False
    pause_btn: bool = False

    # Outputs
    pump:  bool = False
    valve: bool = False

    # Servos
    servo_door:      str = "closed"   # "open" | "closed"
    servo_laser_btn: str = "release"  # "press" | "release"

    # Fault record
    fault: Optional[str] = None

    # Laser-head position proxy (drives TOF-5 sim value)
    laser_head_home: bool = True

    # Config params (subset; expand when persistent-config schema is locked)
    params: Dict[str, Any] = field(default_factory=lambda: {
        "laser_interlock_mode":      0,
        "status_rate_hz":            5,
        "servo_door_open_deg":       90,
        "servo_door_closed_deg":     0,
        "servo_laser_btn_press_deg": 45,
        "servo_laser_btn_release_deg": 0,
    })

    # Protocol counters
    seq:          int   = 0
    uptime_start: float = field(default_factory=time.monotonic)

    # ---- helpers ----------------------------------------------------------

    def uptime_ms(self) -> int:
        return int((time.monotonic() - self.uptime_start) * 1000)

    def state_elapsed(self) -> float:
        return time.monotonic() - self.state_entered_at

    def set_state(self, new_state: State) -> None:
        self.state = new_state
        self.state_entered_at = time.monotonic()

    @property
    def pickup_ok(self) -> bool:
        """Aggregate: all four pickup sensors see something close."""
        return all(
            self.tof_valid[i] and self.tof_dist_mm[i] < 200
            for i in range(4)
        )

    @property
    def material_present(self) -> bool:
        return self.tof_valid[5] and self.tof_dist_mm[5] < 50

    @property
    def laser_safe(self) -> bool:
        mode = self.params.get("laser_interlock_mode", 0)
        # Mode 0: TOF-5 only.  Modes 1-3: extend here.
        return self.laser_head_home

    def resolve_position_name(self) -> Optional[str]:
        """Return the name of the current position if within 1 mm, else None."""
        for name, (x, y, z) in self.taught.items():
            if abs(self.x_mm - x) < 1.0 and abs(self.y_mm - y) < 1.0:
                return name
        return None


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class StateMachine:
    def __init__(self) -> None:
        self.ms        = MachineState()
        self.cmd_queue: queue.Queue = queue.Queue()
        self.out_queue: queue.Queue = queue.Queue()
        self._lock     = threading.Lock()

    # ---- External API (thread-safe) ---------------------------------------

    def enqueue_command(self, msg: dict) -> None:
        self.cmd_queue.put(msg)

    def enqueue_fault(self, reason: str) -> None:
        self.cmd_queue.put({"_internal": "fault", "reason": reason})

    def enqueue_estop(self, released: bool = False) -> None:
        self.cmd_queue.put({"_internal": "estop_hw", "released": released})

    def enqueue_laser_state(self, home: bool) -> None:
        self.cmd_queue.put({"_internal": "laser_state", "home": home})

    def enqueue_material_state(self, present: bool) -> None:
        self.cmd_queue.put({"_internal": "material_state", "present": present})

    def build_status(self) -> dict:
        with self._lock:
            return self._build_status()

    def get_status_rate(self) -> float:
        return float(self.ms.params.get("status_rate_hz", 5))

    def send(self, msg: dict) -> None:
        self.out_queue.put(json.dumps(msg))

    # ---- Main tick (called from the tick thread) --------------------------

    def tick(self) -> None:
        with self._lock:
            self._drain_queue()
            self._tick_transitions()

    # ---- Internal helpers -------------------------------------------------

    def _drain_queue(self) -> None:
        while not self.cmd_queue.empty():
            try:
                msg = self.cmd_queue.get_nowait()
            except queue.Empty:
                break
            if "_internal" in msg:
                self._handle_internal(msg)
            else:
                self._handle_command(msg)

    def _handle_internal(self, msg: dict) -> None:
        ms   = self.ms
        kind = msg["_internal"]

        if kind == "fault":
            reason   = msg["reason"]
            ms.fault = reason
            ms.set_state(State.FAULTED)
            self.send({"type": "fault", "reason": reason})

        elif kind == "estop_hw":
            if msg["released"]:
                ms.estop_hw = False
            else:
                ms.estop_hw = True
                ms.fault    = "estop_triggered"
                ms.set_state(State.ESTOPPED)
                self.send({"type": "fault", "reason": "estop_triggered"})

        elif kind == "laser_state":
            ms.laser_head_home  = msg["home"]
            ms.tof_dist_mm[4]   = 30 if msg["home"] else 400

        elif kind == "material_state":
            # TOF-6 (ch5): < 50 mm = present, > 50 mm = absent
            ms.tof_dist_mm[5] = 25 if msg["present"] else 300
            ms.tof_valid[5]   = True

    def _handle_command(self, msg: dict) -> None:
        ms     = self.ms
        cmd_id = msg.get("id")
        cmd    = msg.get("cmd")

        # Basic framing checks
        if cmd_id is None:
            self.send({"type": "nack", "id": None, "cmd": cmd, "reason": "missing_id"})
            return
        if cmd is None:
            self.send({"type": "nack", "id": cmd_id, "cmd": None, "reason": "malformed"})
            return

        # Unknown command
        if cmd not in ALWAYS_ACCEPT and cmd not in COMMAND_STATES:
            self.send({"type": "nack", "id": cmd_id, "cmd": cmd, "reason": "unknown_cmd"})
            return

        # State matrix
        if cmd not in ALWAYS_ACCEPT:
            allowed = COMMAND_STATES.get(cmd, set())
            if ms.state not in allowed:
                if ms.state == State.ESTOPPED:
                    reason = "estop_active"
                elif ms.state == State.FAULTED:
                    reason = "hw_fault"
                else:
                    reason = "not_ready"
                self.send({"type": "nack", "id": cmd_id, "cmd": cmd, "reason": reason})
                return

        # Dispatch to handler
        handler_name = f"_cmd_{cmd}"
        handler      = getattr(self, handler_name, None)
        if handler:
            handler(cmd_id, msg)
        else:
            # Commands that need no special logic (laser_safe, etc.)
            self.send({"type": "ack", "id": cmd_id, "cmd": cmd})

    def _tick_transitions(self) -> None:
        ms = self.ms

        # Mid-state servo side-effects
        # Release laser button 0.3 s after entering WAITING_FOR_CUT
        if ms.state == State.WAITING_FOR_CUT and ms.state_elapsed() > 0.3:
            ms.servo_laser_btn = "release"

        # Open door 0.5 s into DEPOSITING so cut parts fall through
        if ms.state == State.DEPOSITING and ms.state_elapsed() > 0.5:
            ms.servo_door = "open"

        if ms.state in TIMED_TRANSITIONS:
            duration, next_state = TIMED_TRANSITIONS[ms.state]
            if ms.state_elapsed() >= duration:
                if next_state is None:
                    # DEPOSITING complete — close door, then check job loop
                    ms.servo_door = "closed"
                    ms.job_count += 1
                    if ms.job_total == 0 or ms.job_count < ms.job_total:
                        for i in range(4):
                            ms.tof_dist_mm[i] = 150
                        ms.set_state(State.PICKING)
                    else:
                        for i in range(4):
                            ms.tof_dist_mm[i] = 150
                        ms.job_count = 0
                        ms.job_total = 0
                        ms.set_state(State.READY)
                else:
                    # Simulate workpiece appearing during VERIFY_PICKUP
                    if ms.state == State.VERIFY_PICKUP:
                        for i in range(4):
                            ms.tof_dist_mm[i] = 80
                    # Press laser button at end of PLACING
                    if ms.state == State.PLACING:
                        ms.servo_laser_btn = "press"
                    ms.set_state(next_state)

        elif ms.state == State.WAITING_FOR_LASER_SAFE:
            if ms.laser_safe:
                ms.set_state(State.PLACING)

    # ---- Command handlers ------------------------------------------------

    def _cmd_home(self, cmd_id: int, msg: dict) -> None:
        self.ms.set_state(State.HOMING)
        self.send({"type": "ack", "id": cmd_id, "cmd": "home"})

    def _cmd_start_job(self, cmd_id: int, msg: dict) -> None:
        ms           = self.ms
        ms.job_count = 0
        ms.job_total = int(msg.get("count", 0))
        for i in range(4):
            ms.tof_dist_mm[i] = 150     # no workpiece yet
        ms.set_state(State.PICKING)
        self.send({"type": "ack", "id": cmd_id, "cmd": "start_job"})

    def _cmd_pause(self, cmd_id: int, msg: dict) -> None:
        ms                  = self.ms
        ms.pre_pause_state  = ms.state
        ms.set_state(State.PAUSED)
        self.send({"type": "ack", "id": cmd_id, "cmd": "pause"})

    def _cmd_resume(self, cmd_id: int, msg: dict) -> None:
        ms                 = self.ms
        resume_to          = ms.pre_pause_state or State.READY
        ms.pre_pause_state = None
        ms.set_state(resume_to)
        self.send({"type": "ack", "id": cmd_id, "cmd": "resume"})

    def _cmd_estop(self, cmd_id: int, msg: dict) -> None:
        ms       = self.ms
        ms.fault = "estop_triggered"
        ms.set_state(State.ESTOPPED)
        self.send({"type": "ack",   "id": cmd_id, "cmd": "estop"})
        self.send({"type": "fault", "reason": "estop_triggered"})

    def _cmd_reset_fault(self, cmd_id: int, msg: dict) -> None:
        ms       = self.ms
        ms.fault = None
        ms.set_state(State.IDLE)
        self.send({"type": "ack", "id": cmd_id, "cmd": "reset_fault"})

    def _cmd_reset_estop(self, cmd_id: int, msg: dict) -> None:
        ms = self.ms
        if ms.estop_hw:
            self.send({"type": "nack", "id": cmd_id, "cmd": "reset_estop",
                       "reason": "hw_fault"})
            return
        ms.fault = None
        ms.set_state(State.IDLE)
        self.send({"type": "ack", "id": cmd_id, "cmd": "reset_estop"})

    def _cmd_jog(self, cmd_id: int, msg: dict) -> None:
        ms        = self.ms
        axis      = str(msg.get("axis", "")).upper()
        direction = msg.get("dir", 1)
        distance  = msg.get("distance_mm", 0.0)

        if axis not in ("X", "Y", "Z"):
            self.send({"type": "nack", "id": cmd_id, "cmd": "jog",
                       "reason": "invalid_param"})
            return

        delta = direction * distance
        if   axis == "X": ms.x_mm += delta
        elif axis == "Y": ms.y_mm += delta
        elif axis == "Z": ms.z_mm += delta

        self.send({"type": "ack", "id": cmd_id, "cmd": "jog"})

    def _cmd_teach_position(self, cmd_id: int, msg: dict) -> None:
        ms   = self.ms
        name = msg.get("name", "")
        if name not in NAMED_POSITIONS:
            self.send({"type": "nack", "id": cmd_id, "cmd": "teach_position",
                       "reason": "invalid_param"})
            return
        ms.taught[name] = (ms.x_mm, ms.y_mm, ms.z_mm)
        self.send({"type": "ack", "id": cmd_id, "cmd": "teach_position"})

    def _cmd_query_position(self, cmd_id: int, msg: dict) -> None:
        ms = self.ms
        self.send({
            "type":          "ack",
            "id":            cmd_id,
            "cmd":           "query_position",
            "x_mm":          round(ms.x_mm, 2),
            "y_mm":          round(ms.y_mm, 2),
            "z_mm":          round(ms.z_mm, 2),
            "position_name": ms.resolve_position_name(),
        })

    def _cmd_set_param(self, cmd_id: int, msg: dict) -> None:
        ms    = self.ms
        key   = msg.get("key")
        value = msg.get("value")
        if key is None or key not in ms.params:
            self.send({"type": "nack", "id": cmd_id, "cmd": "set_param",
                       "reason": "invalid_param"})
            return
        ms.params[key] = value
        self.send({"type": "ack", "id": cmd_id, "cmd": "set_param"})

    def _cmd_get_param(self, cmd_id: int, msg: dict) -> None:
        ms  = self.ms
        key = msg.get("key")
        if key is None or key not in ms.params:
            self.send({"type": "nack", "id": cmd_id, "cmd": "get_param",
                       "reason": "invalid_param"})
            return
        self.send({"type": "ack", "id": cmd_id, "cmd": "get_param",
                   "key": key, "value": ms.params[key]})

    def _cmd_save_config(self, cmd_id: int, msg: dict) -> None:
        self.send({"type": "ack", "id": cmd_id, "cmd": "save_config"})

    def _cmd_load_config(self, cmd_id: int, msg: dict) -> None:
        self.send({"type": "ack", "id": cmd_id, "cmd": "load_config"})

    def _cmd_move_to(self, cmd_id: int, msg: dict) -> None:
        ms = self.ms
        x = msg.get("x_mm")
        y = msg.get("y_mm")
        z = msg.get("z_mm")
        if x is None or y is None or z is None:
            self.send({"type": "nack", "id": cmd_id, "cmd": "move_to",
                       "reason": "invalid_param"})
            return
        # Simulator: update position immediately (firmware would animate)
        ms.x_mm, ms.y_mm, ms.z_mm = float(x), float(y), float(z)
        self.send({"type": "ack", "id": cmd_id, "cmd": "move_to"})

    def _cmd_save_position(self, cmd_id: int, msg: dict) -> None:
        ms   = self.ms
        name = msg.get("name", "")
        x    = msg.get("x_mm")
        y    = msg.get("y_mm")
        z    = msg.get("z_mm")
        if name not in NAMED_POSITIONS or None in (x, y, z):
            self.send({"type": "nack", "id": cmd_id, "cmd": "save_position",
                       "reason": "invalid_param"})
            return
        ms.taught[name] = (float(x), float(y), float(z))
        self.send({"type": "ack", "id": cmd_id, "cmd": "save_position"})

    def _cmd_query_status(self, cmd_id: int, msg: dict) -> None:
        self.send(self._build_status())

    def _cmd_query_positions(self, cmd_id: int, msg: dict) -> None:
        ms = self.ms
        self.send({
            "type": "ack",
            "id":   cmd_id,
            "cmd":  "query_positions",
            "positions": {
                name: {"x_mm": round(x, 2), "y_mm": round(y, 2), "z_mm": round(z, 2)}
                for name, (x, y, z) in ms.taught.items()
            },
        })

    def _cmd_query_sensors(self, cmd_id: int, msg: dict) -> None:
        ms = self.ms
        self.send({
            "type": "ack",
            "id":   cmd_id,
            "cmd":  "query_sensors",
            "tof":  [
                {"ch": i, "dist_mm": ms.tof_dist_mm[i], "valid": ms.tof_valid[i]}
                for i in range(6)
            ],
            "outputs": {
                "pump":           ms.pump,
                "valve":          ms.valve,
                "servo_door":     ms.servo_door,
                "servo_laser_btn": ms.servo_laser_btn,
            },
            "inputs":  {
                "estop_hw":  ms.estop_hw,
                "start_btn": ms.start_btn,
                "pause_btn": ms.pause_btn,
            },
        })

    def _cmd_set_output(self, cmd_id: int, msg: dict) -> None:
        ms     = self.ms
        output = msg.get("output", "")
        state  = msg.get("state")
        if output not in VALID_OUTPUTS or not isinstance(state, bool):
            self.send({"type": "nack", "id": cmd_id, "cmd": "set_output",
                       "reason": "invalid_param"})
            return
        setattr(ms, output, state)
        self.send({"type": "ack", "id": cmd_id, "cmd": "set_output"})

    def _cmd_set_servo(self, cmd_id: int, msg: dict) -> None:
        ms       = self.ms
        servo    = msg.get("servo", "")
        position = msg.get("position", "")
        if servo not in VALID_SERVOS or position not in VALID_SERVOS.get(servo, set()):
            self.send({"type": "nack", "id": cmd_id, "cmd": "set_servo",
                       "reason": "invalid_param"})
            return
        setattr(ms, f"servo_{servo}", position)
        self.send({"type": "ack", "id": cmd_id, "cmd": "set_servo"})

    # ---- Status builder --------------------------------------------------

    def _build_status(self) -> dict:
        ms    = self.ms
        ms.seq += 1
        return {
            "type":             "status",
            "seq":              ms.seq,
            "uptime_ms":        ms.uptime_ms(),
            "state":            ms.state.value,
            "position_name":    ms.resolve_position_name(),
            "x_mm":             round(ms.x_mm, 2),
            "y_mm":             round(ms.y_mm, 2),
            "z_mm":             round(ms.z_mm, 2),
            "job_count":        ms.job_count,
            "job_total":        ms.job_total,
            "pickup_ok":        ms.pickup_ok,
            "material_present": ms.material_present,
            "laser_safe":       ms.laser_safe,
            "estop_hw":         ms.estop_hw,
            "fault":            ms.fault,
        }


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------

HOST = "localhost"
PORT = 9999


def _client_reader(conn: socket.socket, sm: StateMachine,
                   stop: threading.Event) -> None:
    """Read newline-delimited JSON from the client and enqueue to the SM."""
    buf = b""
    conn.settimeout(0.1)

    while not stop.is_set():
        try:
            chunk = conn.recv(1024)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            continue
        except OSError:
            break

        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue

            if len(line) > 256:
                try:
                    partial = json.loads(line[:256])
                    cmd_id  = partial.get("id")
                    cmd     = partial.get("cmd")
                except Exception:
                    cmd_id = None
                    cmd    = None
                sm.send({"type": "nack", "id": cmd_id, "cmd": cmd,
                         "reason": "oversized"})
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                sm.send({"type": "nack", "id": None, "cmd": None,
                         "reason": "malformed"})
                continue

            sm.enqueue_command(msg)


def _client_writer(conn: socket.socket, sm: StateMachine,
                   stop: threading.Event) -> None:
    """Drain the SM output queue and write to the client."""
    while not stop.is_set():
        try:
            line = sm.out_queue.get(timeout=0.05)
            conn.sendall((line + "\n").encode())
        except queue.Empty:
            continue
        except OSError:
            break


def run_server(sm: StateMachine, stop: threading.Event) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    srv.settimeout(0.5)
    print(f"[sim] Listening on {HOST}:{PORT}")

    while not stop.is_set():
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            continue

        print(f"[sim] Client connected: {addr}")
        client_stop = threading.Event()
        reader = threading.Thread(
            target=_client_reader, args=(conn, sm, client_stop), daemon=True)
        writer = threading.Thread(
            target=_client_writer, args=(conn, sm, client_stop), daemon=True)
        reader.start()
        writer.start()
        reader.join()
        client_stop.set()
        writer.join()
        conn.close()
        print("[sim] Client disconnected")

    srv.close()


# ---------------------------------------------------------------------------
# Status broadcaster
# ---------------------------------------------------------------------------

def status_broadcaster(sm: StateMachine, stop: threading.Event) -> None:
    while not stop.is_set():
        rate     = sm.get_status_rate()
        interval = 1.0 / max(rate, 0.1)
        sm.send(sm.build_status())
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Console — fault injection and diagnostics
# ---------------------------------------------------------------------------

HELP_TEXT = """
Console commands:
  fault <reason>     Inject a fault
                       pickup_lost | pickup_failed | sensor_timeout
                       sensor_out_of_range | homing_failed | motion_fault
                       laser_interlock | config_invalid | estop_triggered
  estop              Trigger hardware e-stop
  estop_release      Release hardware e-stop
  laser_home         Set laser head to home position (TOF-5 = near)
  laser_busy         Set laser head to away / busy (TOF-5 = far)
  material on        Simulate material present (TOF-6 near)
  material off       Simulate no material remaining (TOF-6 far)
  status             Print machine state to console
  help               Show this help
  quit               Shut down simulator
"""


def console_loop(sm: StateMachine, stop: threading.Event) -> None:
    print(HELP_TEXT)
    while not stop.is_set():
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            stop.set()
            break

        if not line:
            continue

        parts = line.split()
        cmd   = parts[0].lower()

        if cmd == "quit":
            stop.set()

        elif cmd == "fault":
            if len(parts) < 2:
                print(f"Usage: fault <reason>")
            elif parts[1] not in VALID_FAULTS:
                print(f"Unknown reason. Valid: {', '.join(sorted(VALID_FAULTS))}")
            else:
                sm.enqueue_fault(parts[1])
                print(f"[sim] Injected fault: {parts[1]}")

        elif cmd == "estop":
            sm.enqueue_estop(released=False)
            print("[sim] Hardware e-stop triggered")

        elif cmd == "estop_release":
            sm.enqueue_estop(released=True)
            print("[sim] Hardware e-stop released")

        elif cmd == "laser_home":
            sm.enqueue_laser_state(home=True)
            print("[sim] Laser head -> home")

        elif cmd == "laser_busy":
            sm.enqueue_laser_state(home=False)
            print("[sim] Laser head -> away/busy")

        elif cmd == "material":
            if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
                print("Usage: material on | material off")
            else:
                present = parts[1].lower() == "on"
                sm.enqueue_material_state(present)
                state_str = "present" if present else "absent"
                print(f"[sim] Material -> {state_str}")

        elif cmd == "status":
            with sm._lock:
                ms = sm.ms
                print(f"  state          : {ms.state.value}")
                print(f"  position       : x={ms.x_mm:.1f}  y={ms.y_mm:.1f}  z={ms.z_mm:.1f}")
                print(f"  position_name  : {ms.resolve_position_name()}")
                print(f"  job            : {ms.job_count} / {ms.job_total}")
                print(f"  pickup_ok      : {ms.pickup_ok}")
                print(f"  laser_safe     : {ms.laser_safe}  (head_home={ms.laser_head_home})")
                print(f"  estop_hw       : {ms.estop_hw}")
                print(f"  fault          : {ms.fault}")
                print(f"  outputs        : pump={ms.pump}  valve={ms.valve}")
                print(f"  servo_door     : {ms.servo_door}")
                print(f"  servo_laser_btn: {ms.servo_laser_btn}")

        elif cmd == "help":
            print(HELP_TEXT)

        else:
            print(f"Unknown command: '{cmd}'. Type 'help' for options.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

TICK_INTERVAL = 0.05    # 50 ms state machine tick


def main() -> None:
    sm   = StateMachine()
    stop = threading.Event()

    def tick_loop() -> None:
        while not stop.is_set():
            sm.tick()
            time.sleep(TICK_INTERVAL)

    threads = [
        threading.Thread(target=run_server,         args=(sm, stop), daemon=True),
        threading.Thread(target=status_broadcaster,  args=(sm, stop), daemon=True),
        threading.Thread(target=tick_loop,                            daemon=True),
    ]
    for t in threads:
        t.start()

    console_loop(sm, stop)
    print("[sim] Shutting down...")
    stop.set()


if __name__ == "__main__":
    main()
