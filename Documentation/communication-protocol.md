# Communication Protocol (JSON over USB Serial)

The serial protocol is the contract between the Windows GUI and the Mega firmware.
Both sides depend on it. Documenting it as a first-class spec (rather than embedded
in the architecture) lets the GUI be built and tested against a software simulator
before hardware is available, with the same contract dropping onto real firmware
later.

> **Status:** v1.0 — Command set, state × command matrix, reason codes, and status
> message fields are locked. `CALIBRATING` state and stepper calibration commands
> added. Program execution model reflects the interpreter-based architecture
> (see `job-program.md`).

---

## 1. Transport

- **Link:** USB-B serial COM port. Primary development and production PC link.
- **Encoding:** JSON, one message per line (newline-delimited / NDJSON).
- **Line ending:** firmware accepts `\n` and must tolerate `\r\n`.
- **Maximum line length:** 256 bytes for standard messages. `load_program`
  payloads larger than 200 bytes are sent via chunked transfer — see §4.3.
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
| `CALIBRATING` | Stepper calibration in progress. Two phases: (1) traverse to far hard stop, counting steps; (2) awaiting `set_cal_distance` from the GUI. |

**State transition notes:**

- `home` → `HOMING` → `READY` on success, `FAULTED` on failure.
- `run_program` → `RUNNING`. Executor begins at instruction 0 of the stored
  program.
- `pause` during `RUNNING` → `PAUSED`. Executor suspends after the current
  atomic instruction completes.
- `resume` from `PAUSED` → `RUNNING`. Executor continues from where it stopped.
- Program `HALT` instruction → `IDLE`. Clean completion.
- Runtime fault or `FAULT` instruction → `FAULTED`. `reset_fault` → `IDLE`.
  Re-homing required before next run.
- `ESTOPPED` is entered from any state when the hardware e-stop fires or a
  software `estop` command is received. `reset_estop` → `IDLE` (not `READY` —
  position trust is lost).
- `calibrate_axis` → `CALIBRATING`. Firmware homes the axis, then drives it to
  the far hard stop counting steps. Once the traverse completes, `cal_steps` in
  the status broadcast becomes non-zero — the GUI should then prompt the operator
  to enter the actual travel distance. `set_cal_distance` → `IDLE`. Position
  trust is lost; re-home before running.

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

### 4.2 Calibration and teaching

| Command | Additional fields | Description |
|---|---|---|
| `jog` | `axis` *(str)*, `dir` *(int: 1 or -1)*, `distance_mm` *(float)* | Move one axis incrementally. `READY` only. |
| `teach_position` | `name` *(str)* | Save the machine's current coordinates as a named position. `READY` only. |
| `query_position` | — | Return current machine coordinates via ACK. |
| `query_positions` | — | Return all stored named positions with coordinates. ★ always valid. |
| `move_to` | `x_mm`, `y_mm`, `z_mm` *(float)* | Move to absolute coordinates. `READY` only. |
| `save_position` | `name` *(str)*, `x_mm`, `y_mm`, `z_mm` *(float)* | Store coordinates as a named position without moving. `IDLE` and `READY`. |
| `calibrate_axis` | `axis` *(str: `"X"`, `"Y"`, or `"Z"`)* | Begin stepper calibration for one axis. Homes the axis, then drives to the far hard stop using StallGuard, counting steps. → `CALIBRATING`. `IDLE` and `READY` only. |
| `set_cal_distance` | `axis` *(str)*, `mm` *(float)* | Supply the actual travel distance after a successful traverse. Firmware computes `steps_per_mm = raw_steps / mm`, saves to Config/EEPROM, and returns to `IDLE`. Only valid in `CALIBRATING` after traverse completes (`cal_steps > 0` in status). |

**Named positions:** `home`, `laser_a`, `laser_b`, `deposit`.

**Calibration workflow:**

```
GUI sends:  {"cmd": "calibrate_axis", "axis": "X"}
FW returns: {"type": "ack", "cmd": "calibrate_axis"}   ← traverse begins
  ... status broadcasts show state=CALIBRATING, cal_steps=0 while traversing ...
  ... traverse completes ...
  ... status broadcasts show state=CALIBRATING, cal_axis="X", cal_steps=12800 ...
GUI shows dialog: "Measured 12800 steps for X. Enter actual travel distance (mm):"
Operator measures with calipers, types 420.
GUI sends:  {"cmd": "set_cal_distance", "axis": "X", "mm": 420.0}
FW returns: {"type": "ack", "cmd": "set_cal_distance"}
  ... steps_per_mm = 12800 / 420.0 = 30.48, saved to EEPROM, state → IDLE ...
```

Calibration must be run once per axis on first install and after any mechanical
change (belt replacement, pulley change). The computed `steps_per_mm` persists
across power cycles in EEPROM.

### 4.3 Program loading (`load_program`)

The `program` field carries the full job program object as defined in
`job-program.md`. A serialized program of **200 bytes or less** is sent in a
single `load_program` command. Anything larger must use the chunked transfer
sequence below, because standard lines are capped at 256 bytes (§1).

**Single-part (serialized program ≤ 200 bytes):**
```json
{"type": "cmd", "id": 4, "cmd": "load_program", "program": { ... }}
```

**Chunked transfer (serialized program > 200 bytes):**

Chunking uses three dedicated commands rather than repeated `load_program`
messages. The raw program JSON is split into fragments, each base64-encoded
for transport.

| Command | Fields | Description |
|---|---|---|
| `begin_transfer` | `name` *(str)*, `size` *(int)*, `chunks` *(int)* | Start a transfer. `size` is the raw payload length in bytes; `chunks` is the number of fragments to expect. Resets any in-progress transfer. |
| `program_chunk` | `index` *(int)*, `data` *(str)* | One base64-encoded fragment. `index` must equal the number of chunks already accepted (strictly in order, 0-based). ACK echoes the accepted `index`. |
| `end_transfer` | — | Finalize. Firmware decodes, checks length against `size`, parses, and validates the program. |

`begin_transfer` and each `program_chunk` receive their own ACK/NACK. The final
result is delivered as a **`load_program`** ACK/NACK in response to
`end_transfer`.

**Successful ACK (in response to `end_transfer`):**
```json
{"type": "ack", "id": 4, "cmd": "load_program", "instructions": 24, "bytes": 1180}
```

**Transfer-specific NACK reasons** (see also §6.1): `no_transfer_in_progress`,
`out_of_order_expected_N`, `bad_base64`, `incomplete_R_of_C`, `size_mismatch`,
and `json_error:<detail>`.

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

**Calibration params** (read-only via `get_param`; written only by `set_cal_distance`):

| Key | Description |
|---|---|
| `steps_per_mm_x` | X-axis calibration (steps/mm). |
| `steps_per_mm_y` | Y-axis calibration (steps/mm). |
| `steps_per_mm_z` | Z-axis calibration (steps/mm). |

### 4.5 Service and diagnostics

| Command | Additional fields | Description |
|---|---|---|
| `query_status` | — | Request an immediate status message. ★ always valid. |
| `query_sensors` | — | Request a full raw sensor dump. Returns once via ACK. |
| `set_output` | `output` *(str)*, `state` *(bool)* | Manually toggle a named output. `IDLE` and `READY` only. |
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

✓ = accepted. — = rejected (NACK `not_ready` in most states; `hw_fault` in
FAULTED, `estop_active` in ESTOPPED, `calibrating` in CALIBRATING — see §6.1).
★ = accepted in all states.

| Command | IDLE | HOMING | READY | RUNNING | PAUSED | FAULTED | ESTOPPED | CALIBRATING |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `home` | ✓ | — | ✓ | — | — | — | — | — |
| `load_program` | ✓ | — | ✓ | — | — | — | — | — |
| `begin_transfer` | ✓ | — | ✓ | — | — | — | — | — |
| `program_chunk` | ✓ | — | ✓ | — | — | — | — | — |
| `end_transfer` | ✓ | — | ✓ | — | — | — | — | — |
| `get_program` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `run_program` | — | — | ✓ | — | — | — | — | — |
| `pause` | — | — | — | ✓ | — | — | — | — |
| `resume` | — | — | — | — | ✓ | — | — | — |
| `estop` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `reset_fault` | — | — | — | — | — | ✓ | — | — |
| `reset_estop` | — | — | — | — | — | — | ✓ | — |
| `jog` | — | — | ✓ | — | — | — | — | — |
| `teach_position` | — | — | ✓ | — | — | — | — | — |
| `query_position` | ✓ | — | ✓ | — | — | ✓ | ✓ | — |
| `query_positions` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `move_to` | — | — | ✓ | — | — | — | — | — |
| `save_position` | ✓ | — | ✓ | — | — | — | — | — |
| `set_param` | ✓ | — | ✓ | — | — | — | — | — |
| `get_param` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `save_config` | ✓ | — | ✓ | — | — | — | — | — |
| `load_config` | ✓ | — | ✓ | — | — | — | — | — |
| `query_status` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `query_sensors` | ✓ | — | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `set_output` | ✓ | — | ✓ | — | — | — | — | — |
| `set_servo` | ✓ | — | ✓ | — | — | — | — | — |
| `laser_safe` | ★ | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| `calibrate_axis` | ✓ | — | ✓ | — | — | — | — | — |
| `set_cal_distance` | — | — | — | — | — | — | — | ✓ |

---

## 6. Reason codes

### 6.1 NACK reason codes

| Reason | When used |
|---|---|
| `not_ready` | Command is valid but illegal in the current state. |
| `calibrating` | Command rejected because the machine is `CALIBRATING`. |
| `unknown_cmd` | `cmd` field not recognized. |
| `malformed` | JSON failed to parse, or the `cmd` field is missing. |
| `oversized` | Line exceeded the 256-byte limit. |
| `missing_id` | No `id` field. NACK sent with `"id": null`. |
| `invalid_param` | Key unknown, value out of range/wrong type, or program validation failure. |
| `invalid_axis` | `calibrate_axis` received an axis other than `X`, `Y`, or `Z`. |
| `invalid_distance` | `set_cal_distance` received a distance ≤ 0. |
| `traverse_not_done` | `set_cal_distance` received before the calibration traverse completed. |
| `estop_active` | Command rejected because e-stop is active (`ESTOPPED`). |
| `hw_fault` | Command rejected because the machine is `FAULTED`, or `reset_estop` issued while the hardware e-stop is still held. |
| `no_program` | `run_program` issued but no valid program is stored. |
| `buffer_full` | `begin_transfer` size exceeds the firmware's program buffer. |
| `no_transfer_in_progress` | `program_chunk`/`end_transfer` received with no active transfer. |
| `out_of_order_expected_N` | A `program_chunk` arrived with an unexpected `index`; N is the next expected index. |
| `bad_base64` | A `program_chunk` `data` field failed base64 decoding. |
| `incomplete_R_of_C` | `end_transfer` with fewer chunks received (R) than declared (C). |
| `size_mismatch` | Assembled transfer payload length did not match the declared `size`. |
| `json_error:<detail>` | Assembled transfer payload was not valid JSON. |

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
| `laser_not_parked` | Arm move attempted while laser head is not in park position. |
| `cal_traverse_failed` | StallGuard did not trigger during calibration traverse (motor stalled or driver fault). |
| `config_invalid` | EEPROM config failed CRC or schema version mismatch at boot. |
| `estop_triggered` | Hardware e-stop input fired. |

**Program executor faults:**

| Reason | Cause |
|---|---|
| `probe_failed` | `PROBE_Z` did not detect a surface within `probe_max_depth_mm`. |
| `pickup_lost` | Pump ON + arm moving + all pickup ToF sensors (ch0–3) lost the object. |
| `laser_not_parked` | Arm move attempted while ToF ch4 reads out-of-range. |
| `wait_timeout` | `WAIT` condition not met before `timeout_ms` elapsed. |
| `loop_overflow` | A loop exceeded the 10,000-iteration safety limit. |
| `call_depth` | Subroutine call depth exceeded 8 levels. |
| `program_error` | Instruction field missing or undefined variable referenced at runtime. |

---

## 7. Status message fields

### 7.1 Periodic status message

Emitted at 2–10 Hz (configurable). Carries aggregate state.

| Field | Type | Description |
|---|---|---|
| `type` | str | Always `"status"`. |
| `seq` | int | Incrementing sequence number. |
| `uptime_ms` | int | Milliseconds since firmware boot. |
| `state` | str | Current state (from §2). |
| `position_name` | str or null | Named position if at one, otherwise null. |
| `x_mm` | float | Current X position in mm. |
| `y_mm` | float | Current Y position in mm. |
| `z_mm` | float | Current Z position in mm. |
| `pickup_ok` | bool | All four pickup ToF sensors satisfied (aggregate). |
| `material_present` | bool | TOF-6 detects remaining material. |
| `laser_safe` | bool | Computed interlock result from active mode sources. |
| `estop_hw` | bool | Hardware e-stop input state. |
| `program_loaded` | bool | A valid program is stored and passes validation. |
| `fault` | str or null | Current fault reason, or null. |
| `current_op` | str or null | `op` of the executing instruction. Non-null only during `RUNNING` / `PAUSED`. |
| `step_index` | int or null | Flat instruction index. Non-null only during `RUNNING` / `PAUSED`. |
| `loop_iter` | int or null | Current iteration of the innermost active loop. |
| `variables` | object or null | Snapshot of all current program variables. |
| `cal_axis` | str or null | Axis being calibrated (`"X"`, `"Y"`, `"Z"`), or null. Non-null only during `CALIBRATING` after traverse completes. |
| `cal_steps` | int or null | Raw step count from the calibration traverse. Non-null and > 0 only after traverse completes. GUI should show the "enter distance" dialog when this is non-zero. |

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
{"type": "ack", "id": 4, "cmd": "get_param", "key": "steps_per_mm_x", "value": 80.0}
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

---

## 8. Protocol rules

- Every validly framed command receives an ACK or NACK.
- Status messages are periodic at 2–10 Hz (configurable via `set_param`).
- Fault messages are emitted immediately and reflected in the next status.
- GUI command timeout: no ACK/NACK within 500–1000 ms → GUI marks timed out.
- The GUI must tolerate USB disconnects and reconnects. On reconnect, send
  `query_status` then `get_program` before assuming state.
- The firmware must not require a GUI connection to continue a running program.
- GUI commands are requests. The firmware is the runtime authority.
- `set_output` and `set_servo` are rejected during `RUNNING`.

---

## 9. Program and configuration storage

Two independent items live in EEPROM:

**Configuration** — machine calibration: `steps_per_mm` per axis, home offsets,
named position coordinates, sensor thresholds, servo angles, motion parameters.
Managed via `set_param` / `save_config` / `load_config` and written automatically
after a successful `set_cal_distance`. Loaded at boot. Updated infrequently.

**Job program** — the instruction sequence the executor runs. Uploaded via
`load_program`, stored separately from config. Persists across power cycles so
the machine can run headless. Retrieved via `get_program`.

Both config and program include a schema version byte and CRC16. Invalid or
missing data falls back to safe defaults (config) or no-program state (program).

---

## 10. Interpreter and simulator

A host-side Python interpreter (`ProgramInterpreter`) implements the job
program execution model from `job-program.md`. The simulator wraps the
interpreter in the safety layer and exposes it over TCP using this protocol.
The interpreter is the reference implementation for the C++ port on the Mega.
