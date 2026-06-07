# Job Program — Instruction Set Specification

A pick-and-place job is expressed as a **program**: a stored, ordered list of
instructions that the firmware executes sequentially. Programs are authored in
JSON on the GUI, uploaded to the firmware over serial, and persisted in EEPROM
so the machine can re-run the last program headless after power-up.

The instruction set is intentionally machine-agnostic at the language level.
Pick-and-place semantics (probe the stack, pick, place, deposit) are expressed
as sequences of primitives rather than hardcoded states.

> **Status:** v0.1 — instruction set and program format are defined. Binary
> EEPROM encoding and the firmware interpreter are deferred to the firmware
> implementation phase. The Python simulator works directly from JSON.

---

## 1. Concepts

### 1.1 Execution model

The firmware runs a two-layer loop:

```
Safety layer  (always running)
  ├─ checks E-stop and fault conditions between every instruction
  └─ Program executor  (runs only when state = RUNNING)
       └─ fetches next instruction → executes → repeats
```

The program executor cannot suppress or delay the safety layer. E-stop and
hardware faults interrupt execution immediately, regardless of what the program
is doing.

### 1.2 Variables

Programs can declare and use named float or boolean variables. Variables are
local to the current execution (not persisted between runs). They are set by
`PROBE_Z`, `SET_VAR`, and `READ_SENSOR`, and referenced in motion targets and
conditions using the `$name` prefix.

```json
{"op": "SET_VAR", "name": "approach_z", "value": 30.0}
{"op": "MOVE", "x": 100.0, "y": 50.0, "z": "$approach_z"}
```

### 1.3 Conditions

Conditions are strings that evaluate to true or false at runtime.

**Named sensor conditions:**

| Condition | True when |
|---|---|
| `material_present` | TOF-6 reads below the material threshold |
| `pickup_ok` | All four pickup ToF sensors read below pickup threshold |
| `laser_safe` | Current interlock condition is satisfied (per `laser_interlock_mode`) |
| `estop_hw` | Hardware e-stop input is active |

**Variable comparisons:** `"$var op value"` — e.g. `"$loop_count < 10"`.
Supported operators: `==`, `!=`, `<`, `>`, `<=`, `>=`.

**Boolean literals:** `"true"`, `"false"`.

**Negation:** prefix with `not` — e.g. `"not material_present"`.

### 1.4 Named outputs

The same output names used in the communication protocol apply here:

| Name | Type | Values |
|---|---|---|
| `pump` | bool | `true` / `false` |
| `valve` | bool | `true` / `false` |
| `servo_door` | string | `"open"` / `"closed"` |
| `servo_laser_btn` | string | `"press"` / `"release"` |

### 1.5 Subroutines

A program may define named subroutines alongside the main `program` array.
Subroutines are called with `CALL` and return with `RETURN`. They share the
variable scope of their caller. Subroutine depth is limited to 8 levels to
bound stack usage on the Mega.

---

## 2. Program format

Programs are JSON objects with the following top-level structure:

```json
{
  "version": 1,
  "name": "Standard pick-and-place",
  "description": "Optional human-readable description",
  "config": {
    "probe_step_mm":       0.5,
    "probe_max_depth_mm":  200.0,
    "probe_threshold_mm":  40.0,
    "default_speed_pct":   80
  },
  "subroutines": {
    "do_pick":    [ /* instructions */ ],
    "do_deposit": [ /* instructions */ ]
  },
  "program": [ /* instructions */ ]
}
```

### 2.1 `config` fields

| Key | Default | Description |
|---|---|---|
| `probe_step_mm` | 0.5 | Z step size during probe descent. |
| `probe_max_depth_mm` | 200.0 | Maximum probe travel. Fault if surface not found within this distance. |
| `probe_threshold_mm` | 40.0 | ToF reading below this value = surface detected. |
| `default_speed_pct` | 80 | Default motion speed as % of configured max. |

---

## 3. Instruction reference

Every instruction is a JSON object with an `"op"` field. Additional fields
are instruction-specific. Unknown fields are ignored (forward compatibility).

---

### 3.1 Motion

#### `MOVE`

Move to an absolute position. Optional intermediate waypoints are visited in
order before the final destination.

```json
{
  "op":    "MOVE",
  "x":     150.0,
  "y":     80.0,
  "z":     30.0,
  "via":   [{"x": 100.0, "y": 50.0, "z": 60.0}],
  "speed": 60
}
```

| Field | Required | Description |
|---|---|---|
| `x`, `y`, `z` | Yes | Destination in mm. Any may be a `$variable` reference. |
| `via` | No | Array of intermediate waypoints, each with `x`, `y`, `z`. |
| `speed` | No | Speed override as % of max (1–100). Defaults to `config.default_speed_pct`. |

The instruction completes when the final destination is reached. Faults on
travel limit violation or stall.

---

#### `PROBE_Z`

Move to the given X/Y position at a safe approach height, then descend in
`config.probe_step_mm` increments, sampling the pickup ToF sensors each step.
When a sensor reading falls below `config.probe_threshold_mm`, descent stops
and the confirmed Z is stored in `store`.

```json
{
  "op":          "PROBE_Z",
  "x":           100.0,
  "y":           50.0,
  "approach_z":  60.0,
  "store":       "home_z"
}
```

| Field | Required | Description |
|---|---|---|
| `x`, `y` | Yes | XY position to probe at. |
| `approach_z` | Yes | Z height to move to before beginning descent. Must be above the expected surface. |
| `store` | Yes | Variable name to write the detected Z into. |

Faults with `probe_failed` if no surface is detected within
`config.probe_max_depth_mm` of travel.

---

#### `HOME`

Run the homing sequence on the specified axes.

```json
{
  "op":   "HOME",
  "axes": ["X", "Y", "Z"]
}
```

| Field | Required | Description |
|---|---|---|
| `axes` | Yes | Array of axes to home. Valid values: `"X"`, `"Y"`, `"Z"`. Order follows firmware homing sequence. |

Faults with `homing_failed` if an endstop is not found within travel limits.

---

### 3.2 I/O

#### `OUTPUT`

Set a named output to a value.

```json
{"op": "OUTPUT", "name": "pump",          "value": true}
{"op": "OUTPUT", "name": "servo_door",    "value": "open"}
{"op": "OUTPUT", "name": "servo_laser_btn", "value": "press"}
```

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Output name (see §1.4). |
| `value` | Yes | Value appropriate for the output type (bool or string). |

Executes immediately and completes synchronously (no waiting for mechanical
confirmation). Follow with `DELAY` if settling time is needed.

---

#### `READ_SENSOR`

Read a named sensor value into a variable.

```json
{"op": "READ_SENSOR", "sensor": "tof_ch5_mm", "store": "deposit_surface"}
```

| Field | Required | Description |
|---|---|---|
| `sensor` | Yes | Sensor identifier. |
| `store` | Yes | Variable name to write into. |

**Valid sensor identifiers:**

| Sensor | Returns | Description |
|---|---|---|
| `tof_ch0_mm` .. `tof_ch5_mm` | float | Raw distance reading for ToF channel 0–5 in mm. |
| `material_present` | bool | Aggregate material detection (TOF-6 < threshold). |
| `pickup_ok` | bool | Aggregate pickup confirmation. |
| `laser_safe` | bool | Current interlock result. |

---

### 3.3 Wait and timing

#### `WAIT`

Block until a condition becomes true. Optionally fault if the condition is not
met within a timeout.

```json
{
  "op":        "WAIT",
  "condition": "laser_safe",
  "timeout_ms": 60000,
  "timeout_fault": "laser_interlock"
}
```

| Field | Required | Description |
|---|---|---|
| `condition` | Yes | Condition string (see §1.3). |
| `timeout_ms` | No | Maximum wait time in ms. No timeout if omitted. |
| `timeout_fault` | No | Fault reason to trigger on timeout. Defaults to `wait_timeout`. |

The safety layer remains active during WAIT. E-stop and hardware faults
interrupt it immediately.

---

#### `DELAY`

Wait a fixed duration unconditionally.

```json
{"op": "DELAY", "ms": 500}
```

| Field | Required | Description |
|---|---|---|
| `ms` | Yes | Duration in milliseconds. |

---

### 3.4 Flow control

#### `LOOP_WHILE`

Execute the body repeatedly while a condition is true. Checked before each
iteration (may execute zero times). A safety limit of 10,000 iterations is
enforced regardless of condition; exceeding it faults with `loop_overflow`.

```json
{
  "op":        "LOOP_WHILE",
  "condition": "material_present",
  "body":      [ /* instructions */ ]
}
```

| Field | Required | Description |
|---|---|---|
| `condition` | Yes | Condition string (see §1.3). |
| `body` | Yes | Array of instructions to execute each iteration. |

---

#### `LOOP_FOR`

Execute the body a fixed number of times. The loop counter is available as
`$_loop_i` within the body (0-indexed).

```json
{
  "op":    "LOOP_FOR",
  "count": 5,
  "body":  [ /* instructions */ ]
}
```

| Field | Required | Description |
|---|---|---|
| `count` | Yes | Number of iterations. May be a `$variable` reference. |
| `body` | Yes | Array of instructions. |

---

#### `IF` / `ELSE`

Conditional execution.

```json
{
  "op":       "IF",
  "condition": "pickup_ok",
  "then":     [ /* instructions */ ],
  "else":     [ /* instructions */ ]
}
```

| Field | Required | Description |
|---|---|---|
| `condition` | Yes | Condition string. |
| `then` | Yes | Instructions executed when condition is true. |
| `else` | No | Instructions executed when condition is false. |

---

#### `CALL`

Call a named subroutine. Execution continues after the matching `RETURN` (or
end of subroutine body). Maximum call depth: 8.

```json
{"op": "CALL", "sub": "do_pick"}
```

---

#### `RETURN`

Return from the current subroutine. If called in the main program body, halts
execution cleanly (equivalent to `HALT`).

```json
{"op": "RETURN"}
```

---

#### `JUMP` / `LABEL`

Unconditional jump to a label. Primarily for simple loops without a nesting
structure. Prefer `LOOP_WHILE` when possible.

```json
{"op": "LABEL", "name": "top_of_loop"}
{"op": "JUMP",  "to":   "top_of_loop"}
```

JUMP is subject to the same 10,000-execution safety limit as `LOOP_WHILE`.

---

### 3.5 Program control

#### `HALT`

End the program successfully. State transitions to `READY`.

```json
{"op": "HALT"}
```

---

#### `FAULT`

Trigger a named fault and halt immediately.

```json
{"op": "FAULT", "reason": "no_material_at_start"}
```

`reason` can be any string; it appears in the fault report and GUI fault
display.

---

### 3.6 Variables

#### `SET_VAR`

Set a named variable to a literal value or a simple arithmetic expression.

```json
{"op": "SET_VAR", "name": "approach_z",  "value": 60.0}
{"op": "SET_VAR", "name": "sheet_count", "value": 0}
{"op": "SET_VAR", "name": "sheet_count", "expr":  "$sheet_count + 1"}
```

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Variable name (alphanumeric + underscore, no leading digit). |
| `value` | One of | Literal float or bool. |
| `expr` | One of | Arithmetic expression string. Operators: `+` `-` `*` `/`. References: `$varname`. |

---

#### `LOG`

Emit a message to the GUI communications log. Useful for debugging programs
without hardware.

```json
{"op": "LOG", "message": "Starting deposit cycle $sheet_count"}
```

Variable references in `message` are expanded at runtime.

---

## 4. Status fields added by the executor

While `state = RUNNING`, the periodic status message gains additional fields:

| Field | Type | Description |
|---|---|---|
| `step_index` | int | Index of the currently executing instruction in the flattened program. |
| `current_op` | str | `op` value of the current instruction. |
| `loop_iter` | int | Current iteration count of the innermost active loop. |
| `variables` | object | Current variable name → value snapshot. |

---

## 5. Protocol additions

### 5.1 New commands

| Command | Fields | Description | Valid states |
|---|---|---|---|
| `load_program` | `program` *(object)* | Upload and validate a program. Firmware stores to EEPROM on success. | `IDLE`, `READY` |
| `get_program` | — | Return the currently stored program JSON. | ★ all |
| `run_program` | — | Start executing the stored program. Transitions to `RUNNING`. | `READY` |

`start_job` is retired in favour of `run_program`. `run_program` takes no
count argument — termination is controlled by the program itself (typically
a `LOOP_WHILE material_present`).

### 5.2 New fault reasons

| Reason | Cause |
|---|---|
| `probe_failed` | `PROBE_Z` did not detect a surface within `probe_max_depth_mm`. |
| `wait_timeout` | `WAIT` timed out before condition became true. |
| `loop_overflow` | A loop exceeded the 10,000-iteration safety limit. |
| `call_depth` | Subroutine call depth exceeded 8 levels. |
| `program_error` | Instruction field missing or invalid at runtime (e.g. undefined variable). |
| `no_program` | `run_program` issued but no valid program is stored. |

### 5.3 `load_program` ACK / NACK

On success:
```json
{"type": "ack", "id": 1, "cmd": "load_program", "instructions": 24, "bytes": 1180}
```

On failure (validation error):
```json
{
  "type":   "nack",
  "id":     1,
  "cmd":    "load_program",
  "reason": "invalid_param",
  "detail": "Instruction 7: PROBE_Z missing required field 'store'"
}
```

---

## 6. Safety rules

1. The safety layer checks E-stop and fault conditions between every
   instruction. A program cannot prevent this check.
2. `PROBE_Z` will not descend below `probe_max_depth_mm` regardless of sensor
   readings. This is a firmware hard limit, not a configurable value.
3. `MOVE` enforces configured travel limits. Instructions that command motion
   outside limits are faulted at runtime, not at load time.
4. `LOOP_WHILE` and `JUMP` are limited to 10,000 total iterations per program
   run to prevent unrecoverable infinite loops. The limit resets on each
   `run_program`.
5. Subroutine depth is limited to 8. Exceeding this faults immediately.
6. A `FAULT` instruction is always honoured; no instruction can suppress it.
7. Programs are validated at load time (field presence, type checks, known op
   codes, subroutine references). Invalid programs are rejected before storage.

---

## 7. Example program

Standard pick-and-place cycle running until material is exhausted:

```json
{
  "version": 1,
  "name": "Standard cycle",
  "config": {
    "probe_step_mm":      0.5,
    "probe_max_depth_mm": 200.0,
    "probe_threshold_mm": 40.0
  },
  "subroutines": {
    "do_pick": [
      {"op": "PROBE_Z",  "x": "$home_x", "y": "$home_y",
       "approach_z": 60.0, "store": "home_z"},
      {"op": "MOVE",     "x": "$home_x", "y": "$home_y", "z": "$home_z"},
      {"op": "OUTPUT",   "name": "pump",  "value": true},
      {"op": "DELAY",    "ms": 400},
      {"op": "IF", "condition": "not pickup_ok",
       "then": [{"op": "FAULT", "reason": "pickup_failed"}]},
      {"op": "MOVE",     "x": "$home_x", "y": "$home_y", "z": 60.0}
    ],
    "do_laser_a": [
      {"op": "MOVE",     "x": 150.0, "y": 80.0, "z": 30.0,
       "via": [{"x": 80.0, "y": 80.0, "z": 50.0}]},
      {"op": "OUTPUT",   "name": "servo_door", "value": "closed"},
      {"op": "WAIT",     "condition": "laser_safe", "timeout_ms": 30000,
       "timeout_fault": "laser_interlock"},
      {"op": "MOVE",     "x": 150.0, "y": 80.0, "z": 5.0},
      {"op": "OUTPUT",   "name": "pump", "value": false},
      {"op": "OUTPUT",   "name": "servo_laser_btn", "value": "press"},
      {"op": "DELAY",    "ms": 300},
      {"op": "OUTPUT",   "name": "servo_laser_btn", "value": "release"},
      {"op": "WAIT",     "condition": "laser_safe", "timeout_ms": 120000,
       "timeout_fault": "laser_interlock"},
      {"op": "MOVE",     "x": 150.0, "y": 80.0, "z": 30.0}
    ],
    "do_deposit": [
      {"op": "PROBE_Z",  "x": "$deposit_x", "y": "$deposit_y",
       "approach_z": 60.0, "store": "deposit_z"},
      {"op": "MOVE",     "x": "$deposit_x", "y": "$deposit_y", "z": "$deposit_z"},
      {"op": "OUTPUT",   "name": "valve", "value": true},
      {"op": "DELAY",    "ms": 300},
      {"op": "OUTPUT",   "name": "valve", "value": false},
      {"op": "OUTPUT",   "name": "servo_door", "value": "open"},
      {"op": "DELAY",    "ms": 500},
      {"op": "OUTPUT",   "name": "servo_door", "value": "closed"},
      {"op": "MOVE",     "x": "$deposit_x", "y": "$deposit_y", "z": 60.0},
      {"op": "SET_VAR",  "name": "sheet_count", "expr": "$sheet_count + 1"},
      {"op": "LOG",      "message": "Sheet $sheet_count deposited"}
    ]
  },
  "program": [
    {"op": "HOME",     "axes": ["X", "Y", "Z"]},
    {"op": "SET_VAR",  "name": "home_x",    "value": 100.0},
    {"op": "SET_VAR",  "name": "home_y",    "value": 50.0},
    {"op": "SET_VAR",  "name": "deposit_x", "value": 50.0},
    {"op": "SET_VAR",  "name": "deposit_y", "value": 200.0},
    {"op": "SET_VAR",  "name": "sheet_count", "value": 0},
    {"op": "IF", "condition": "not material_present",
     "then": [{"op": "FAULT", "reason": "no_material_at_start"}]},
    {"op": "LOOP_WHILE", "condition": "material_present", "body": [
      {"op": "CALL", "sub": "do_pick"},
      {"op": "CALL", "sub": "do_laser_a"},
      {"op": "CALL", "sub": "do_deposit"}
    ]},
    {"op": "LOG",   "message": "Job complete. $sheet_count sheets processed."},
    {"op": "HALT"}
  ]
}
```

---

## 8. EEPROM storage (deferred)

The JSON format is the source of truth during the GUI and simulator phase.
Binary EEPROM encoding (opcode table, compact field encoding, CRC) will be
specified when firmware implementation begins. The interpreter in the simulator
works directly from the parsed JSON object.

Key constraints for the binary format:
- Must fit within 4 KB Mega EEPROM alongside config (target: ≤ 3 KB for program)
- Fixed-width instruction records preferred for O(1) random access
- CRC32 over the program block, checked at load and at `run_program`
- Schema version byte for future format changes
