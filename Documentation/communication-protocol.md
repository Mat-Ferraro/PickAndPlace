# Communication Protocol (JSON over USB Serial)

The serial protocol is the contract between the Windows GUI and the Mega firmware.
Both sides depend on it. Documenting it as a first-class spec (rather than embedded
in the architecture) lets the GUI be built and tested against a software simulator
before hardware is available, with the same contract dropping onto real firmware
later.

> **Status:** v0.8 — Command set, state × command matrix, reason codes, and status
> message fields are locked. This document is complete enough to build the simulator
> and GUI against.

---

## 1. Transport

- **Link:** USB-B serial COM port. Primary development and production PC link.
- **Encoding:** JSON, one message per line (newline-delimited / NDJSON).
- **Line ending:** firmware accepts `\n` and must tolerate `\r\n`.
- **Maximum line length:** 256 bytes. The firmware may NACK or drop any line
  exceeding this limit.
- **Character set:** ASCII / UTF-8 JSON. No binary payloads.
- **Parser rule:** no unbounded strings, no deeply nested objects, no dynamic
  allocation in the main control loop.
- RS-485 is not required for the current architecture; revisit if needed later.

---

## 2. State definitions

The firmware state machine uses the following named states. The protocol uses these
exact strings in all `state` fields.

| State | Meaning |
|---|---|
| `IDLE` | Powered on. Not yet homed. Position unknown. |
| `HOMING` | Homing sequence in progress. |
| `READY` | Homed. Position known. Awaiting job or calibration commands. |
| `PICKING` | Moving to pick up uncut material. |
| `VERIFY_PICKUP` | Verifying pickup via ToF sensors. |
| `MOVING_TO_LASER` | Transporting material to laser position. |
| `WAITING_FOR_LASER_SAFE` | Waiting for laser-safe condition before entering workspace. |
| `PLACING` | Placing material at the laser position. |
| `WAITING_FOR_CUT` | Waiting for laser cut to complete. |
| `RETRIEVING` | Retrieving cut material from laser position. |
| `DEPOSITING` | Depositing finished part. |
| `PAUSED` | Motion stopped at a safe point. Resumable. |
| `FAULTED` | A fault has occurred. Operator intervention required. |
| `ESTOPPED` | E-stop is active. All motion stopped. |

**State transition notes:**

- `ESTOPPED` is entered from any state when the hardware e-stop input fires or a
  software `estop` command is received. `reset_estop` returns to `IDLE` (not
  `READY`) — position trust is lost and the machine must re-home.
- `FAULTED` is entered from any state on a detected fault. `reset_fault` returns
  to `IDLE` — motion faults leave position suspect, and re-homing is required before
  resuming a job.
- `PAUSED` stores the pre-pause state internally. On `resume`, the firmware
  re-evaluates any pending conditions (e.g. laser-safe) before proceeding.

---

## 3. Message types

All messages are single JSON objects, one per line.

### 3.1 Command (GUI → firmware)

```json
{"type": "cmd", "id": 1, "cmd": "start_job"}
{"type": "cmd", "id": 2, "cmd": "jog", "axis": "X", "dir": 1, "distance_mm": 5.0}
{"type": "cmd", "id": 3, "cmd": "set_param", "key": "laser_interlock_mode", "value": 0}
```

- `id`: monotonically increasing integer from the GUI. Firmware echoes it in the
  ACK or NACK.
- `cmd`: command string from the command set in §4.
- Additional fields are command-specific (see §4).

### 3.2 ACK (firmware → GUI)

```json
{"type": "ack", "id": 1, "cmd": "start_job"}
```

### 3.3 NACK (firmware → GUI)

```json
{"type": "nack", "id": 1, "cmd": "start_job", "reason": "not_ready"}
```

### 3.4 Status (firmware → GUI, periodic)

```json
{"type": "status", "state": "READY", ...}
```

Full field set defined in §7.

### 3.5 Fault (firmware → GUI, immediate)

```json
{"type": "fault", "reason": "pickup_lost"}
```

Emitted immediately when a fault occurs and also reflected in the next periodic
status message. Fault reason codes are defined in §6.2.

---

## 4. Command set

### 4.1 Job control

| Command | Additional fields | Description |
|---|---|---|
| `home` | — | Begin the homing sequence. Transitions to `HOMING`. |
| `start_job` | `count` *(optional int)* | Begin the pick-and-place cycle. Transitions to `PICKING`. `count` is the number of pieces to run; omit or pass `0` for an indefinite run (machine cycles until paused, e-stopped, or faulted). |
| `pause` | — | Stop motion at the next safe point. Transitions to `PAUSED`. |
| `resume` | — | Resume from `PAUSED`. Returns to the pre-pause state. |
| `estop` | — | Software e-stop. Transitions to `ESTOPPED` immediately. |
| `reset_fault` | — | Clear `FAULTED`. Transitions to `IDLE`. Re-homing required before next job. |
| `reset_estop` | — | Clear `ESTOPPED` after the hardware e-stop input is physically released. Transitions to `IDLE`. |

### 4.2 Calibration and teaching

| Command | Additional fields | Description |
|---|---|---|
| `jog` | `axis` *(str)*, `dir` *(int: 1 or -1)*, `distance_mm` *(float)* | Move one axis by a delta. Only valid in `READY`. |
| `teach_position` | `name` *(str)* | Save current machine coordinates as a named position. Only valid in `READY`. |
| `query_position` | — | Request current machine coordinates. Returns via ACK with position fields. |
| `query_positions` | — | Request all stored named positions with their x/y/z coordinates. Always valid (★). |
| `move_to` | `x_mm`, `y_mm`, `z_mm` *(float)* | Move the arm to absolute coordinates. Only valid in `READY`. |
| `save_position` | `name` *(str)*, `x_mm`, `y_mm`, `z_mm` *(float)* | Store coordinates as a named position without moving. Valid in `IDLE` and `READY`. |

**Named positions:** `home`, `laser_a`, `laser_b`, `deposit`. These names are fixed
in the firmware; `teach_position` writes to whichever name is specified.

> **Note on teaching `home`:** the `home` position is normally established by the
> homing sequence (endstop contacts) and stored as an offset. Manually teaching
> `home` via `teach_position` overrides that offset. Only do this intentionally
> during calibration — a bad home position will affect all subsequent motion.

### 4.3 Configuration

| Command | Additional fields | Description |
|---|---|---|
| `set_param` | `key` *(str)*, `value` | Set one configuration parameter. Does not write to EEPROM until `save_config`. |
| `get_param` | `key` *(str)* | Read one configuration parameter. Firmware responds via ACK with a `value` field. |
| `save_config` | — | Explicitly write the current configuration to EEPROM. |
| `load_config` | — | Reload configuration from EEPROM, discarding any unsaved `set_param` changes. |

**Configuration parameters** include: `laser_interlock_mode`, home offsets, pickup
sensor thresholds, remaining-material threshold, motion speed/accel limits, axis
inversion flags, steps-per-mm, homing behavior, status rate, and servo angle
parameters. Full parameter key list to be defined when the persistent-config schema
is locked.

**Servo config params** (set via `set_param`, angles in degrees):

| Key | Default | Description |
|---|---|---|
| `servo_door_open_deg` | 90 | Door servo angle for the open position. |
| `servo_door_closed_deg` | 0 | Door servo angle for the closed position. |
| `servo_laser_btn_press_deg` | 45 | Laser button servo angle for the press position. |
| `servo_laser_btn_release_deg` | 0 | Laser button servo angle for the release position. |

### 4.4 Service and diagnostics

| Command | Additional fields | Description |
|---|---|---|
| `query_status` | — | Request an immediate status message. Always valid. |
| `query_sensors` | — | Request a full raw sensor dump. Returns once via ACK; not a continuous stream. |
| `set_output` | `output` *(str)*, `state` *(bool)* | Manually toggle a named output (pump or valve). For bench testing only. |
| `set_servo` | `servo` *(str)*, `position` *(str)* | Move a servo to a named position. For bench testing and calibration. |

**Named outputs for `set_output`:** `pump`, `valve`.

**Named servos and positions for `set_servo`:**

| Servo | Valid positions | Function |
|---|---|---|
| `door` | `"open"` / `"closed"` | Opens to allow cut paper to fall through during deposit. |
| `laser_btn` | `"press"` / `"release"` | Presses the laser cutter start button. Actuated automatically at the end of `PLACING`. |

Servo angles for each named position are stored as config parameters and are
adjustable without firmware changes.

### 4.5 Laser interlock

| Command | Additional fields | Description |
|---|---|---|
| `laser_safe` | — | Operator confirmation that the laser workspace is safe. No-op in mode 0; reserved for modes 1 and 3. Always accepted and ACKed in all states. |

**Laser interlock modes** (set via `set_param`, key `laser_interlock_mode`):

| Mode | Firmware requires before entering laser workspace |
|---|---|
| `0` | TOF-5 home confirmation only. Default. Headless-safe. |
| `1` | TOF-5 + `laser_safe` command from GUI. |
| `2` | TOF-5 + dry-contact input pin. *(Future — input not yet wired.)* |
| `3` | TOF-5 + dry-contact + `laser_safe` command. *(Future.)* |

---

## 5. State × command matrix

✓ = accepted and processed. — = NACKed with `reason: not_ready`. ★ = accepted in
all states.

| Command | IDLE | HOMING | READY | PICKING | VERIFY_PICKUP | MOVING_TO_LASER | WAITING_LASER_SAFE | PLACING | WAITING_FOR_CUT | RETRIEVING | DEPOSITING | PAUSED | FAULTED | ESTOPPED |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `home` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `start_job` | — | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `pause` | — | — | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `resume` | — | — | — | — | — | — | — | — | — | — | — | ✓ | — | — |
| `estop` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `reset_fault` | — | — | — | — | — | — | — | — | — | — | — | — | ✓ | — |
| `reset_estop` | — | — | — | — | — | — | — | — | — | — | — | — | — | ✓ |
| `jog` | — | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `teach_position` | — | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `query_position` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | ✓ | ✓ | ✓ |
| `set_param` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `get_param` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `save_config` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `load_config` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `query_status` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `query_positions` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `move_to` | — | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `save_position` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `query_sensors` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | ✓ | ✓ | ✓ |
| `set_output` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `set_servo` | ✓ | — | ✓ | — | — | — | — | — | — | — | — | — | — | — |
| `laser_safe` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |

---

## 6. Reason codes

### 6.1 NACK reason codes

Used in `{"type": "nack", "id": 1, "cmd": "...", "reason": "..."}`.

| Reason | When it is used |
|---|---|
| `not_ready` | Command is valid but illegal in the current state (matrix says —). |
| `unknown_cmd` | The `cmd` field value is not recognized. |
| `malformed` | JSON failed to parse, or a required field is missing or wrong type. |
| `oversized` | Line exceeded the 256-byte limit. |
| `missing_id` | No `id` field present. NACK is still sent with `"id": null`. |
| `invalid_param` | `set_param` / `get_param` key is unknown, or value is out of range or wrong type. |
| `estop_active` | Command requires motion but e-stop is active. |
| `hw_fault` | A hardware condition (e.g. driver fault) prevents the command. |

Note: `estop_active` and `hw_fault` are technically covered by `not_ready` since
`ESTOPPED` and `FAULTED` are matrix states, but specific codes let the GUI display
a meaningful reason rather than a generic "not ready."

### 6.2 Fault reason codes

Used in `{"type": "fault", "reason": "..."}` and in the `fault` field of status
messages.

| Reason | What happened |
|---|---|
| `pickup_lost` | Workpiece dropped during transit — ToF sensors lost it mid-move. |
| `pickup_failed` | Pickup attempted but ToF verification never confirmed contact. |
| `sensor_timeout` | A ToF sensor stopped responding entirely (wiring or power issue). |
| `sensor_out_of_range` | A ToF sensor is responding but its reading is outside expected bounds. |
| `homing_failed` | Homing sequence did not find an endstop within travel limits. |
| `motion_fault` | Unexpected limit hit during travel, or stall detected. |
| `laser_interlock` | Machine attempted to enter laser workspace but interlock condition was not met. |
| `config_invalid` | EEPROM config failed CRC check or schema version mismatch at boot. |
| `estop_triggered` | Hardware e-stop input fired. Fault record explaining why machine entered `ESTOPPED`. |

---

## 7. Status message fields

### 7.1 Periodic status message

Emitted at 2–10 Hz (configurable). Carries aggregate state — raw sensor detail is
reserved for explicit `query_sensors` requests.

| Field | Type | Description |
|---|---|---|
| `type` | str | Always `"status"`. |
| `seq` | int | Incrementing sequence number. GUI uses this to detect missed messages. |
| `uptime_ms` | int | Milliseconds since firmware boot. Detects reboots on reconnect. |
| `state` | str | Current state name (from §2). |
| `position_name` | str or null | Named position if at one (`"home"`, `"laser_a"`, `"laser_b"`, `"deposit"`), otherwise null. |
| `x_mm` | float | Current X position in mm. |
| `y_mm` | float | Current Y position in mm. |
| `z_mm` | float | Current Z position in mm. |
| `job_count` | int | Pieces completed this run. |
| `job_total` | int | Total pieces requested. 0 if no count was specified. |
| `pickup_ok` | bool | True if all four pickup ToF sensors are satisfied (aggregate). |
| `material_present` | bool | True if TOF-6 detects remaining uncut material. |
| `laser_safe` | bool | Computed interlock result from all active mode sources. |
| `estop_hw` | bool | Hardware e-stop input state. |
| `fault` | str or null | Current fault reason code, or null if no fault active. |

Example (~160 bytes, within 256-byte line limit at 10 Hz):

```json
{
  "type": "status",
  "seq": 42,
  "uptime_ms": 12345,
  "state": "MOVING_TO_LASER",
  "position_name": null,
  "x_mm": 120.5,
  "y_mm": 45.0,
  "z_mm": 10.0,
  "job_count": 2,
  "job_total": 10,
  "pickup_ok": true,
  "material_present": true,
  "laser_safe": false,
  "estop_hw": false,
  "fault": null
}
```

### 7.2 `query_sensors` ACK

Returned once in response to a `query_sensors` command. Not a stream.

| Field | Type | Description |
|---|---|---|
| `type` | str | `"ack"` |
| `id` | int | Echoed command id. |
| `cmd` | str | `"query_sensors"` |
| `tof` | array[6] | Raw ToF readings. Each entry: `{"ch": 0, "dist_mm": 142, "valid": true}`. Channels 0–3 are pickup sensors, 4 is laser-head home, 5 is remaining-material. |
| `outputs` | object | Current output and servo states: `{"pump": false, "valve": false, "servo_door": "closed", "servo_laser_btn": "release"}`. |
| `inputs` | object | Raw input states: `{"estop_hw": false, "start_btn": false, "pause_btn": false}`. |

Example:

```json
{
  "type": "ack",
  "id": 7,
  "cmd": "query_sensors",
  "tof": [
    {"ch": 0, "dist_mm": 142, "valid": true},
    {"ch": 1, "dist_mm": 139, "valid": true},
    {"ch": 2, "dist_mm": 145, "valid": true},
    {"ch": 3, "dist_mm": 141, "valid": true},
    {"ch": 4, "dist_mm": 312, "valid": true},
    {"ch": 5, "dist_mm": 28,  "valid": true}
  ],
  "outputs": {"pump": false, "valve": false, "servo_door": "closed", "servo_laser_btn": "release"},
  "inputs": {"estop_hw": false, "start_btn": false, "pause_btn": false}
}
```

### 7.3 ACK payloads for data-returning commands

**`query_position`:**

```json
{
  "type": "ack",
  "id": 3,
  "cmd": "query_position",
  "x_mm": 120.5,
  "y_mm": 45.0,
  "z_mm": 10.0,
  "position_name": null
}
```

**`get_param`:**

```json
{
  "type": "ack",
  "id": 4,
  "cmd": "get_param",
  "key": "laser_interlock_mode",
  "value": 0
}
```

**`query_positions`:**

```json
{
  "type": "ack",
  "id": 5,
  "cmd": "query_positions",
  "positions": {
    "home":    {"x_mm": 0.0,   "y_mm": 0.0,   "z_mm": 0.0},
    "laser_a": {"x_mm": 150.0, "y_mm": 80.0,  "z_mm": 10.0},
    "laser_b": {"x_mm": 200.0, "y_mm": 80.0,  "z_mm": 10.0},
    "deposit": {"x_mm": 50.0,  "y_mm": 200.0, "z_mm": 5.0}
  }
}
```

---

## 8. Protocol rules

- Every validly framed command receives an ACK or a NACK.
- Malformed JSON, oversized lines, missing `id`, missing `cmd`, unknown commands,
  and commands illegal in the current state are all rejected. If an `id` can be
  parsed, respond with a NACK using that `id`; otherwise emit a parser/error event
  without blocking the machine.
- Status messages are periodic. Initial target: **2–10 Hz**, configurable via
  `set_param`. Do not stream full raw sensor dumps during normal running; reserve
  those for explicit `query_sensors` requests in service mode.
- Fault messages are emitted immediately when they occur and are also reflected in
  the next status message.
- GUI command timeout: if no ACK/NACK arrives within **500–1000 ms**, the GUI marks
  the command timed out and may query status or retry if the command is retry-safe.
- **Retry-safe commands:** `query_status`, `get_param`, `query_sensors`,
  `query_position`. These are read-only and safe to re-send if a response is lost.
  All write/action commands are not retry-safe without operator awareness.
- The GUI must tolerate USB disconnects and reconnects. On reconnect, send
  `query_status` before assuming any state.
- The firmware must not require a GUI connection to continue a running job.
- GUI commands are requests. The firmware is the runtime authority and decides
  whether to honor, ignore, or NACK based on current state and safety rules.

---

## 9. Job and configuration transfer

The firmware must have all information needed to finish or safely stop a job before
the laptop disconnects. The planned approach is **hybrid**:

- **Critical calibration lives in EEPROM** on the Mega: home offsets, named
  positions, sensor thresholds, motion parameters, servo positions. Loaded at boot.
- **Job-specific options come from the GUI** before `start_job`: piece count, and
  any per-run overrides.
- Any EEPROM write is always explicit (`save_config`), versioned, and validated.
  EEPROM is never written from streaming `set_param` updates — only on `save_config`.

---

## 10. Simulator

Before GUI development, a host-side Python simulator will implement this protocol
and run a fake state machine with timed transitions and injectable faults. The
simulator serves as the reference state machine implementation, regression target
for the GUI, and the starting point for porting logic to the Mega firmware.
