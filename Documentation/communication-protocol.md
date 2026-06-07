# Communication Protocol (JSON over USB Serial)

The serial protocol is the contract between the Windows GUI and the Mega firmware.
Both sides depend on it. Documenting it as a first-class spec (rather than embedded
in the architecture) lets the GUI be built and tested against a software simulator
before hardware is available, with the same contract dropping onto real firmware
later.

> **Status:** v0.9 — Command set, state × command matrix, reason codes, and status
> message fields are locked. Program execution model updated to reflect the
> interpreter-based architecture (see `job-program.md`).

---

## 1. Transport

- **Link:** USB-B serial COM port. Primary development and production PC link.
- **Encoding:** JSON, one message per line (newline-delimited / NDJSON).
- **Line ending:** firmware accepts `\n` and must tolerate `\r\n`.
- **Maximum line length:** 256 bytes for standard messages. `load_program`
  payloads are chunked — see §4.3.
- **Character set:** ASCII / UTF-8 JSON. No binary payloads.
- **Parser rule:** no unbounded strings, no deeply nested objects, no dynamic
  allocation in the main control loop.
- RS-485 is not required for the current architecture; revisit if needed later.

---

## 2. State definitions

The firmware state machine uses the following named states. The protocol uses
these exact strings in all `state` fields.

The detailed mid-job states from earlier revisions (PICKING, VERIFY_PICKUP,
MOVING_TO_LASER, etc.) have been retired. That sequencing is now expressed
inside the job program itself (see `job-program.md`) and reported to the GUI
via the `current_op` and `step_index` status fields.

| State | Meaning |
|---|---|
| `IDLE` | Powered on. Not yet homed. Position unknown. |
| `HOMING` | Homing sequence in progress. |
| `READY` | Homed. Position known. No program running. Awaiting command. |
| `RUNNING` | Program executor is active. |
| `PAUSED` | Executor suspended at a safe point. Resumable. |
| `FAULTED` | A fault has occurred. Operator intervention required. |
| `ESTOPPED` | E-stop active. All motion stopped. |

**State transition notes:**

- `home` → `HOMING` → `READY` on success, `FAULTED` on failure.
- `run_program` → `RUNNING`. Executor begins at instruction 0 of the stored
  program.
- `pause` during `RUNNING` → `PAUSED`. Executor suspends after the current
  atomic instruction completes.
- `resume` from `PAUSED` → `RUNNING`. Executor continues from where it stopped.
- Program `HALT` instruction → `READY`. Clean completion.
- Runtime fault or `FAULT` instruction → `FAULTED`. `reset_fault` → `IDLE`.
  Re-homing required before next run.
- `ESTOPPED` is entered from any state when the hardware e-stop fires or a
  software `estop` command is received. `reset_estop` → `IDLE` (not `READY` —
  position trust is lost).

---

## 3. Message types

All messages are single JSON objects, one per line.

### 3.1 Command (GUI → firmware)

```json
{"type": "cmd", "id": 1, "cmd": "run_program"}
{"type": "cmd", "id": 2, "cmd": "jog", "axis": "X", "dir": 1, "distance_mm": 5.0}
{"type": "cmd", "id": 3, "cmd": "set_param", "key": "laser_interlock_mode", "value": 0}
```

- `id`: monotonically increasing integer from the GUI. Echoed in ACK/NACK.
- `cmd`: command string from the command set in §4.
- Additional fields are command-specific.

### 3.2 ACK (firmware → GUI)

```json
{"type": "ack", "id": 1, "cmd": "run_program"}
```

### 3.3 NACK (firmware → GUI)

```json
{"type": "nack", "id": 1, "cmd": "run_program", "reason": "no_program"}
```

### 3.4 Status (firmware → GUI, periodic)

```json
{"type": "status", "state": "RUNNING", ...}
```

Full field set defined in §7.

### 3.5 Fault (firmware → GUI, immediate)

```json
{"type": "fault", "reason": "probe_failed"}
```

Emitted immediately when a fault occurs and also reflected in the next status
message.

---

## 4. Command set

### 4.1 Job control

| Command | Additional fields | Description |
|---|---|---|
| `home` | — | Begin the homing sequence. → `HOMING`. |
| `load_program` | `program` *(object)* | Upload, validate, and store a job program. See §4.3. |
| `get_program` | — | Return the currently stored program JSON. |
| `run_program` | — | Start executing the stored program. → `RUNNING`. NACKs with `no_program` if none is stored. |
| `pause` | — | Suspend the executor at the next safe point. → `PAUSED`. |
| `resume` | — | Resume the executor. → `RUNNING`. |
| `estop` | — | Software e-stop. → `ESTOPPED` immediately. |
| `reset_fault` | — | Clear `FAULTED`. → `IDLE`. Re-home before next run. |
| `reset_estop` | — | Clear `ESTOPPED` after hardware e-stop is released. → `IDLE`. |

> **`start_job` is retired.** Use `run_program`. Job termination is controlled
> by the program itself (typically `LOOP_WHILE material_present` ending with
> `HALT`). There is no piece count argument.

### 4.2 Calibration and teaching

| Command | Additional fields | Description |
|---|---|---|
| `jog` | `axis` *(str)*, `dir` *(int: 1 or -1)*, `distance_mm` *(float)* | Move one axis incrementally. `READY` only. |
| `teach_position` | `name` *(str)* | Save the machine's current coordinates as a named position. `READY` only. |
| `query_position` | — | Return current machine coordinates via ACK. |
| `query_positions` | — | Return all stored named positions with coordinates. ★ always valid. |
| `move_to` | `x_mm`, `y_mm`, `z_mm` *(float)* | Move to absolute coordinates. `READY` only. |
| `save_position` | `name` *(str)*, `x_mm`, `y_mm`, `z_mm` *(float)* | Store coordinates as a named position without moving. `IDLE` and `READY`. |

**Named positions:** `home`, `laser_a`, `laser_b`, `deposit`.

> **Note on teaching `home`:** the home position is normally established by the
> homing sequence. Teaching it manually overrides the offset. Only do this
> intentionally during calibration.

### 4.3 Program loading (`load_program`)

The `program` field carries the full job program object as defined in
`job-program.md`. Because programs may exceed the 256-byte line limit, the GUI
must chunk large programs and send them in a multi-part sequence:

**Single-part (small programs ≤ 200 bytes serialized):**
```json
{"type": "cmd", "id": 4, "cmd": "load_program", "program": { ... }}
```

**Multi-part (chunked transfer):**
```json
{"type": "cmd", "id": 4, "cmd": "load_program", "chunk": 0, "total_chunks": 3, "data": "<base64 fragment>"}
{"type": "cmd", "id": 5, "cmd": "load_program", "chunk": 1, "total_chunks": 3, "data": "<base64 fragment>"}
{"type": "cmd", "id": 6, "cmd": "load_program", "chunk": 2, "total_chunks": 3, "data": "<base64 fragment>"}
```

Firmware assembles chunks, validates the complete program, then ACKs or NACKs.
An interrupted chunk sequence times out after 5 seconds and is discarded.

**Successful ACK:**
```json
{"type": "ack", "id": 6, "cmd": "load_program", "instructions": 24, "bytes": 1180}
```

**Validation NACK:**
```json
{
  "type":   "nack",
  "id":     6,
  "cmd":    "load_program",
  "reason": "invalid_param",
  "detail": "Instruction 7: PROBE_Z missing required field 'store'"
}
```

### 4.4 Configuration

| Command | Additional fields | Description |
|---|---|---|
| `set_param` | `key` *(str)*, `value` | Set one config parameter. Not written to EEPROM until `save_config`. |
| `get_param` | `key` *(str)* | Read one config parameter. |
| `save_config` | — | Write current config to EEPROM explicitly. |
| `load_config` | — | Reload config from EEPROM, discarding unsaved changes. |

**Servo config params** (angles in degrees):

| Key | Default | Description |
|---|---|---|
| `servo_door_open_deg` | 90 | Door servo open angle. |
| `servo_door_closed_deg` | 0 | Door servo closed angle. |
| `servo_laser_btn_press_deg` | 45 | Laser button press angle. |
| `servo_laser_btn_release_deg` | 0 | Laser button release angle. |
| `laser_interlock_mode` | 0 | Interlock source selection (see §4.6). |
| `status_rate_hz` | 5 | Periodic status rate (2–10 Hz). |

Full parameter key list to be defined when the persistent-config schema is locked.

### 4.5 Service and diagnostics

| Command | Additional fields | Description |
|---|---|---|
| `query_status` | — | Request an immediate status message. ★ always valid. |
| `query_sensors` | — | Request a full raw sensor dump. Returns once via ACK. |
| `set_output` | `output` *(str)*, `state` *(bool)* | Manually toggle a named output. `IDLE` and `READY` only — program owns outputs during `RUNNING`. |
| `set_servo` | `servo` *(str)*, `position` *(str)* | Move a servo to a named position. `IDLE` and `READY` only. |

**Named outputs for `set_output`:** `pump`, `valve`.

**Named servos and positions for `set_servo`:**

| Servo | Valid positions | Function |
|---|---|---|
| `door` | `"open"` / `"closed"` | Opens gate so cut paper falls through during deposit. |
| `laser_btn` | `"press"` / `"release"` | Presses laser cutter start button. |

### 4.6 Laser interlock

| Command | Additional fields | Description |
|---|---|---|
| `laser_safe` | — | Operator confirmation that laser workspace is safe. No-op in mode 0. ★ always valid. |

**Laser interlock modes** (`laser_interlock_mode` param):

| Mode | Requires before entering laser workspace |
|---|---|
| `0` | TOF-5 home confirmation only. Default. Headless-safe. |
| `1` | TOF-5 + `laser_safe` command from GUI. |
| `2` | TOF-5 + dry-contact input. *(Future.)* |
| `3` | TOF-5 + dry-contact + `laser_safe`. *(Future.)* |

---

## 5. State × command matrix

✓ = accepted. — = NACKed `not_ready`. ★ = accepted in all states.

| Command | IDLE | HOMING | READY | RUNNING | PAUSED | FAULTED | ESTOPPED |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `home` | ✓ | — | ✓ | — | — | — | — |
| `load_program` | ✓ | — | ✓ | — | — | — | — |
| `get_program` | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `run_program` | — | — | ✓ | — | — | — | — |
| `pause` | — | — | — | ✓ | — | — | — |
| `resume` | — | — | — | — | ✓ | — | — |
| `estop` | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `reset_fault` | — | — | — | — | — | ✓ | — |
| `reset_estop` | — | — | — | — | — | — | ✓ |
| `jog` | — | — | ✓ | — | — | — | — |
| `teach_position` | — | — | ✓ | — | — | — | — |
| `query_position` | ✓ | — | ✓ | — | — | ✓ | ✓ |
| `query_positions` | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `move_to` | — | — | ✓ | — | — | — | — |
| `save_position` | ✓ | — | ✓ | — | — | — | — |
| `set_param` | ✓ | — | ✓ | — | — | — | — |
| `get_param` | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `save_config` | ✓ | — | ✓ | — | — | — | — |
| `load_config` | ✓ | — | ✓ | — | — | — | — |
| `query_status` | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `query_sensors` | ✓ | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| `set_output` | ✓ | — | ✓ | — | — | — | — |
| `set_servo` | ✓ | — | ✓ | — | — | — | — |
| `laser_safe` | ★ | ★ | ★ | ★ | ★ | ★ | ★ |

---

## 6. Reason codes

### 6.1 NACK reason codes

| Reason | When used |
|---|---|
| `not_ready` | Command is valid but illegal in the current state. |
| `unknown_cmd` | `cmd` field not recognized. |
| `malformed` | JSON failed to parse, or a required field is missing or wrong type. |
| `oversized` | Line exceeded the 256-byte limit. |
| `missing_id` | No `id` field. NACK sent with `"id": null`. |
| `invalid_param` | Key unknown, value out of range/wrong type, or program validation failure. |
| `estop_active` | Command requires motion but e-stop is active. |
| `hw_fault` | A hardware condition prevents the command. |
| `no_program` | `run_program` issued but no valid program is stored in EEPROM. |

### 6.2 Fault reason codes

**Machine faults (hardware / safety):**

| Reason | Cause |
|---|---|
| `pickup_lost` | Workpiece dropped during transit. |
| `pickup_failed` | Pickup attempted but ToF verification never confirmed. |
| `sensor_timeout` | A ToF sensor stopped responding. |
| `sensor_out_of_range` | A ToF sensor reading outside expected bounds. |
| `homing_failed` | Homing sequence did not find an endstop within travel limits. |
| `motion_fault` | Unexpected limit hit or stall during travel. |
| `laser_interlock` | Machine attempted laser workspace entry but interlock not met. |
| `config_invalid` | EEPROM config failed CRC or schema version mismatch at boot. |
| `estop_triggered` | Hardware e-stop input fired. |

**Program executor faults:**

| Reason | Cause |
|---|---|
| `probe_failed`      | `PROBE_Z` did not detect a surface within `probe_max_depth_mm`. |
| `pickup_lost`       | Pump ON + arm moving + all pickup ToF sensors (ch0–3) lost the object. Motion aborted immediately. |
| `laser_not_parked`  | Arm move attempted or in progress while laser head not in park position (ToF ch4 out of range). Motion aborted. |
| `wait_timeout` | `WAIT` condition not met before `timeout_ms` elapsed. |
| `loop_overflow` | A loop exceeded the 10,000-iteration safety limit. |
| `call_depth` | Subroutine call depth exceeded 8 levels. |
| `program_error` | Instruction field missing or undefined variable referenced at runtime. |
| `no_material_at_start` | Program-defined fault — no material detected before job began. |

The last entry is an example of a user-defined fault reason from a `FAULT`
instruction. Any string is valid as a fault reason; the ones above are
firmware-defined and have fixed meanings.

---

## 7. Status message fields

### 7.1 Periodic status message

Emitted at 2–10 Hz (configurable). Carries aggregate state. Raw sensor detail
goes in `query_sensors`.

| Field | Type | Description |
|---|---|---|
| `type` | str | Always `"status"`. |
| `seq` | int | Incrementing sequence number. |
| `uptime_ms` | int | Milliseconds since firmware boot. Detects reboots on reconnect. |
| `state` | str | Current state (from §2). |
| `position_name` | str or null | Named position if at one, otherwise null. |
| `x_mm` | float | Current X position in mm. |
| `y_mm` | float | Current Y position in mm. |
| `z_mm` | float | Current Z position in mm. |
| `pickup_ok` | bool | All four pickup ToF sensors satisfied (aggregate). |
| `material_present` | bool | TOF-6 detects remaining material. |
| `laser_safe` | bool | Computed interlock result from active mode sources. |
| `estop_hw` | bool | Hardware e-stop input state. |
| `fault` | str or null | Current fault reason, or null. |
| `current_op` | str or null | `op` of the executing instruction. Non-null only during `RUNNING` / `PAUSED`. |
| `step_index` | int or null | Flat instruction index in the program. Non-null only during `RUNNING` / `PAUSED`. |
| `loop_iter` | int or null | Current iteration of the innermost active loop. Null when not in a loop. |
| `variables` | object or null | Snapshot of all current program variables. Non-null only during `RUNNING` / `PAUSED`. |

Example during a run:

```json
{
  "type":             "status",
  "seq":              142,
  "uptime_ms":        34210,
  "state":            "RUNNING",
  "position_name":    null,
  "x_mm":             100.5,
  "y_mm":             50.0,
  "z_mm":             28.3,
  "pickup_ok":        true,
  "material_present": true,
  "laser_safe":       false,
  "estop_hw":         false,
  "fault":            null,
  "current_op":       "PROBE_Z",
  "step_index":       8,
  "loop_iter":        3,
  "variables":        {"home_x": 100.0, "home_y": 50.0, "home_z": 28.3, "sheet_count": 3}
}
```

### 7.2 `query_sensors` ACK

| Field | Type | Description |
|---|---|---|
| `type` | str | `"ack"` |
| `id` | int | Echoed command id. |
| `cmd` | str | `"query_sensors"` |
| `tof` | array[6] | Raw ToF readings. Each: `{"ch": 0, "dist_mm": 142, "valid": true}`. Ch 0–3 pickup, ch 4 laser head home, ch 5 material. |
| `outputs` | object | `{"pump": false, "valve": false, "servo_door": "closed", "servo_laser_btn": "release"}` |
| `inputs` | object | `{"estop_hw": false, "start_btn": false, "pause_btn": false}` |

### 7.3 ACK payloads for data-returning commands

**`query_position`:**
```json
{"type": "ack", "id": 3, "cmd": "query_position",
 "x_mm": 120.5, "y_mm": 45.0, "z_mm": 10.0, "position_name": null}
```

**`get_param`:**
```json
{"type": "ack", "id": 4, "cmd": "get_param", "key": "laser_interlock_mode", "value": 0}
```

**`query_positions`:**
```json
{
  "type": "ack", "id": 5, "cmd": "query_positions",
  "positions": {
    "home":    {"x_mm": 0.0,   "y_mm": 0.0,   "z_mm": 0.0},
    "laser_a": {"x_mm": 150.0, "y_mm": 80.0,  "z_mm": 10.0},
    "laser_b": {"x_mm": 200.0, "y_mm": 80.0,  "z_mm": 10.0},
    "deposit": {"x_mm": 50.0,  "y_mm": 200.0, "z_mm": 5.0}
  }
}
```

**`get_program`:**
```json
{"type": "ack", "id": 6, "cmd": "get_program", "program": { ... }}
```

---

## 8. Protocol rules

- Every validly framed command receives an ACK or NACK.
- Malformed JSON, oversized lines, missing `id`/`cmd`, unknown commands, and
  commands illegal in the current state are all rejected.
- Status messages are periodic at 2–10 Hz (configurable via `set_param`).
  Raw sensor dumps are reserved for explicit `query_sensors` requests.
- Fault messages are emitted immediately and also reflected in the next status.
- GUI command timeout: no ACK/NACK within 500–1000 ms → GUI marks timed out.
  May retry read-only commands (`query_status`, `get_param`, `query_sensors`,
  `query_position`, `query_positions`, `get_program`).
- The GUI must tolerate USB disconnects and reconnects. On reconnect, send
  `query_status` then `get_program` before assuming state.
- The firmware must not require a GUI connection to continue a running program.
- GUI commands are requests. The firmware is the runtime authority.
- `set_output` and `set_servo` are rejected during `RUNNING` — the program
  executor owns outputs while running. Manual overrides must wait until `READY`.

---

## 9. Program and configuration storage

Two independent items live in EEPROM:

**Configuration** — machine calibration: home offsets, named position
coordinates, sensor thresholds, servo angles, motion parameters. Managed via
`set_param` / `save_config` / `load_config`. Loaded at boot. Updated
infrequently.

**Job program** — the instruction sequence the executor runs. Uploaded via
`load_program`, stored separately from config. Persists across power cycles so
the machine can run headless without a GUI connection. Retrieved via
`get_program`. The previously loaded program is always available for headless
re-run after power-up.

On first boot (no stored program): `run_program` NACKs with `no_program`.
The GUI must upload a program before the machine can run.

Both config and program include a schema version byte and CRC32. Invalid or
missing data falls back to safe defaults (config) or no-program state (program).

---

## 10. Interpreter and simulator

A host-side Python interpreter (`ProgramInterpreter`) implements the job
program execution model from `job-program.md`. The simulator wraps the
interpreter in the safety layer and exposes it over TCP using this protocol.
The interpreter is the reference implementation that later ports to C++ on
the Mega.
