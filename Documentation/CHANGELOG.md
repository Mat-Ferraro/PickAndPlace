## v1.2

### GUI — ToF sensor offset calibration

**New `tab_sensor_cal.py` — `SensorCalTab`:**
- New "Sensor Cal" tab between Service and Comms.
- Arm sensors table (ch0–ch3): live reading, stored baseline, computed
  clearance (live − baseline), and BLOCKED/CLEAR status per channel.
- Touch threshold spinbox (default 10 mm): clearance ≤ threshold → BLOCKED (red),
  otherwise CLEAR (green).
- "Refresh Readings" button sends `query_sensors` on demand.
- Calibration instructions panel + "Read Baseline" button → sends
  `calibrate_sensors` command; ack populates baseline column and updates
  the status label.
- `set_baseline(ch, value)` method — called from main window after
  `get_param tof_offset_N` to restore persisted baselines on connect.
- Status sensors section (ch4–ch5): read-only display with PARKED/AWAY and
  PRESENT/EMPTY labels; no calibration needed (threshold-based only).

**`pnp_gui.py`:**
- `SensorCalTab` added between Service and Comms tabs.
- `calibrate_sensors` added to `_LOG_CMDS`.
- `calibrate_sensors` ack routes to `sensor_cal_tab.on_cal_ack(offsets)` and
  triggers an immediate `query_sensors` refresh.
- `query_sensors` responses routed to `sensor_cal_tab.on_sensors()`.
- On connect: queries `get_param tof_offset_0..3` to restore persisted
  baselines; responses call `set_baseline(ch, value)`.

### Simulator — sensor calibration support

**`simulator.py`:**
- `calibrate_sensors` added to `COMMAND_STATES` (IDLE/READY only).
- `MachineState` gains `tof_offsets: list` (4 entries, initially `None`).
- `_cmd_calibrate_sensors`: reads `ms.tof_dist_mm[0:4]`, stores in
  `ms.tof_offsets`, returns `{"offsets": [...]}` in the ack. Does not
  modify ch4 or ch5.
- `_cmd_get_param` extended: `tof_offset_N` keys return the stored offset
  for channel N (None if not yet calibrated).

### Tests

- `tests/test_simulator.py` +13 tests (now 221 total):
  `TestSensorCalibration` — command gating (idle/ready/running), reads
  ch0–ch3 from live tof values, excludes ch4/ch5, stores offsets, second
  calibration overwrites first, `get_param` returns None before cal and
  correct values after, all four channels independently verified, ch4
  unchanged by calibration, initial offsets are None.

### Design rationale (recorded for future HAL implementation)

Clearance = live_reading − stored_baseline. When something blocks a
recessed sensor at the baseline distance, clearance ≈ 0 → BLOCKED.
When clear, the sensor reads the far wall and clearance >> 0 → CLEAR.
pickup_ok (existing status field) is the aggregate of all 4 arm channels
being BLOCKED simultaneously. The Config struct will need `tofOffsetMm[4]`
alongside `stepsPerMm[3]` for EEPROM persistence — recorded in
`open-decisions.md`.

## v1.1

### GUI — stepper calibration workflow

**`tab_service.py`:**
- New full-width "Stepper Calibration" group added to the Service tab.
- Three axes (X/Y/Z) show current `steps/mm` in green, or "Not calibrated"
  in orange when zero.
- "Calibrate Axis" button (enabled in IDLE/READY) sends `calibrate_axis`;
  disabled during CALIBRATING to prevent re-triggering mid-traverse.
- Distance-entry panel (hidden by default): appears automatically when
  `on_status()` receives `state=CALIBRATING` with `cal_steps > 0`. Shows the
  raw step count and prompts the operator to enter actual travel distance.
- "Apply" button sends `set_cal_distance` and immediately updates the display
  optimistically. Panel hides when state leaves CALIBRATING.
- `_update_controls()` extended for CALIBRATING state with contextual hint text.
- `set_steps_per_mm(axis, value)` public method — called from main window to
  update the displayed values from `get_param` responses.

**`pnp_gui.py`:**
- `calibrate_axis` and `set_cal_distance` added to `_LOG_CMDS` with dynamic
  messages (includes axis name and distance value).
- On connect: queries `get_param steps_per_mm_x/y/z` to populate persisted
  calibration values from firmware.
- Ack handling: `get_param` responses for calibration keys call
  `set_steps_per_mm()`; `set_cal_distance` ack re-queries all three to refresh.
- Nack handling: calibration nacks show a `QMessageBox.warning`.
- CALIBRATING state flows through status broadcast → tab update without any
  special-case branching in the main window.

### Simulator — stepper calibration support

**`simulator.py`:**
- `CALIBRATING` added to the `State` enum.
- `calibrate_axis` gated to IDLE/READY; `set_cal_distance` gated to CALIBRATING.
- `MachineState` gains `cal_axis`, `cal_raw_steps`, and `steps_per_mm` dict.
- `_cmd_calibrate_axis`: sets CALIBRATING, stores axis, starts a 2-second
  simulated traverse timer.
- `_tick_calibrating`: when timer fires, populates `cal_raw_steps` with
  axis-specific fake values (X/Y: 12800, Z: 6400); stays CALIBRATING
  waiting for `set_cal_distance`.
- `_cmd_set_cal_distance`: validates traverse complete and distance > 0,
  computes `steps_per_mm = raw_steps / mm`, stores per axis, returns to IDLE.
- `_cmd_get_param` extended: `steps_per_mm_x/y/z` keys return values from
  `ms.steps_per_mm` dict; all other keys use the existing params dict.
- `calibrating` added as a gating nack reason.
- `_build_status` includes `cal_axis` and `cal_steps` when traverse is complete.

### Tests

- `tests/test_simulator.py` +20 tests (now 209 total):
  `TestStepperCalibration` — command gating (idle/ready/running/bad axis),
  state transitions (enter CALIBRATING, axis stored, traverse timing,
  axis-specific step counts), `set_cal_distance` (compute, reject before
  traverse, reject zero distance, return to IDLE), multi-axis independence,
  status broadcast (cal fields absent/present), `get_param` for calibration
  keys (before and after calibration).

## v1.0

### Firmware — core logic complete, host-tested

**State machine (C++ port of `simulator.py`):**
- 8-state enum (`State.h`): IDLE, HOMING, READY, RUNNING, PAUSED, FAULTED,
  ESTOPPED, and new CALIBRATING state for automated steps/mm calibration.
- Full command-gating table ported from Python `COMMAND_STATES` /
  `ALWAYS_ACCEPT`. All gating decisions move string literals to flash via
  `PNP_STREQ` chain (no table in SRAM).
- Physical-button path (no-ack), hardware E-stop edge, fault injection all
  ported and tested.

**Interpreter (C++ port of `interpreter.py`):**
- All 18 ops implemented: MOVE (with waypoints), PROBE_Z, HOME, OUTPUT,
  READ_SENSOR, WAIT (with timeout/flip), DELAY, LOOP_FOR (`_loop_i`),
  LOOP_WHILE (overflow guard), IF/else, CALL/RETURN (depth limit), SET_VAR
  (expression evaluator), LOG ($var expansion), HALT, FAULT, JUMP (unsupported,
  correct error), LABEL.
- Condition evaluator: `true`/`false`, `not`, named sensors, `$var op rhs`.
- Arithmetic evaluator: recursive descent (+, -, *, /, parentheses); no `eval`.
- `AbortFlags` struct replaces `threading.Event` for pause/stop.

**ProgramValidator (C++ port):**
- Validates JSON structure, version, all required fields per op, CALL →
  subroutine existence, nested bodies.
- `kRequired` table eliminated — replaced with `PNP_STREQ` chain (saves ~450
  bytes SRAM).
- Accepts `JsonObjectConst` directly (no redundant second JSON parse).

**ProgramStore — chunked transfer:**
- `begin_transfer` / `program_chunk` (base64 decode) / `end_transfer`
  (validate + store).
- Raw JSON buffer is `malloc`'d in `beginTransfer`, freed immediately after
  ArduinoJson parses it — zero BSS cost (was 2 KB static).
- `const char*` input forces ArduinoJson to copy strings; document is
  self-contained after free.

**Calibration system:**
- New `calibrate_axis` / `set_cal_distance` commands.
- `IMachine::traverseToStop(axis, outSteps)` — drives axis to far hard stop
  via StallGuard, returns raw step count.
- `set_cal_distance` computes `steps_per_mm = raw_steps / mm`, stores in
  `StateMachine::stepsPerMm_[3]`.
- No mechanical spec knowledge needed — system measures itself.
- Config/EEPROM persistence pending (next milestone).

**SRAM optimisation (Arduino Mega, 8 KB total):**
- Global variables: 7627 bytes (93%) → 2700 bytes (~33%) after all changes.
- Key changes: heap-allocated transfer buffer, eliminated `kRequired` table,
  `PNP_STREQ`/`PNP_SNPRINTF` PROGMEM macros throughout, `kMaxVars` 16 → 8.
- `Platform.h` defines `PNP_STREQ` and `PNP_SNPRINTF`: `strcmp_P`/`snprintf_P`
  + `PSTR` on AVR; `strcmp`/`snprintf` on host.

**Host test suite:**
- 101 tests, 0 failures (32 state machine + 69 interpreter/validator).
- `cd Firmware/test && make` — builds two test binaries with g++/Unity against
  vendored ArduinoJson and MockMachine.
- All tests mutation-verified.

**Arduino IDE compatibility fixes (AVR-GCC quirks):**
- `Response` struct — added explicit constructor (no aggregate init with
  default members in older dialect).
- `strtof` not available in AVR libc — replaced with `(float)strtod`.
- `<initializer_list>` not available on AVR — replaced range-for brace lists
  with plain C array loops.
- All PROGMEM string handling via `Platform.h` macros.

**Documentation:**
- `communication-protocol.md` updated to v1.0: CALIBRATING state, calibrate_axis
  / set_cal_distance commands, new NACK reasons, cal_axis / cal_steps status
  fields, updated state × command matrix.
- `firmware-architecture.md` rewritten to reflect current state (was scaffold
  docs from v0.9).
- `open-decisions.md` updated: closed chunked transfer, pump driver, persistent
  config architecture, steps/mm calibration mechanism; added ToF calibration,
  re-calibration policy, Config/EEPROM implementation as new items.


## v0.9.3

- **Simulator gains physical-button emulation.** New `press start|pause` console
  command (and `enqueue_button`) drives the headless button map (`architecture.md`
  §9.1) as a real button would: it changes state with **no** command ack, so an
  attached GUI updates purely from the next status broadcast — exercising the
  GUI's externally-driven update path.
- **Fault/error injection extended for GUI testing.** Added `jam [axis]`
  (StallGuard-style stall → `motion_fault` with axis), fixed `estop_release` to
  return ESTOPPED → IDLE per the design, made `reset_fault` re-arm the stop event,
  and stopped the aborting interpreter from overwriting a `motion_fault`/E-stop
  reason. `program_loaded` is now reported in the status message.
- **GUI:** the Run tab now trusts the authoritative `program_loaded` status flag
  (covers a program already in EEPROM / loaded headless), not just its own load ack.
  (The Run tab already re-derived banner + button states from every status
  broadcast, so physical-button-driven changes were already reflected.)
- **Tests:** +15 (now 189 total) covering button emulation, no-ack behaviour,
  jam/motion_fault, e-stop release, reset re-arming, every injectable fault, and the
  `program_loaded` field.
- **Machine geometry resolved:** Cartesian gantry confirmed (not an articulated
  arm); moved to resolved in `open-decisions.md`, `architecture.md` §2 updated.

## v0.9.2

- **Headless control model defined.** Latched E-stop + Start/Pause buttons now form
  a complete no-GUI control surface (Start = proceed: home/run/resume; Pause = halt/
  dismiss: pause/clear-fault; E-stop release → IDLE). Buttons synthesize the same
  internal commands as the serial parser. Specified in `architecture.md` §9.1.
- **Status indicators added to the design.** External RGB status LED, program-loaded
  LED, and a beeper (onboard LED is under the shield). `architecture.md` §9.2.
- **`program_loaded` added** to the status message (`communication-protocol.md`
  §7.1): firmware reports whether a valid program is in EEPROM; gates `run_program`
  and the headless Start button.
- **Stall/jam/homing detection committed to TMC2209 UART + StallGuard4.** Sensorless
  homing (DIAG-on-endstop) plus in-motion jam detection → `motion_fault`; added as a
  third continuous safety monitor (`architecture.md` §11). UART promoted from
  "later" to "now."
- **Dual-Y resolved to independent squaring.** Y2 driven from the E0 socket with its
  own STEP/DIR; Y1/Y2 home to separate DIAG lines so each side squares to its own
  stall.
- **`pin-mapping.md` rebuilt** around the above: Y2 on E0; endstop headers carry
  E-stop (D3) + StallGuard DIAG (Y1 D2, Y2 D18, X+Z OR'd D19); shared single-wire
  TMC UART; Start/Pause + RGB LED + program LED + beeper on spare GPIO; pump/valve on
  D8/D9; added a pin-budget summary.
- **`open-decisions.md`:** moved dual-Y, homing strategy, TMC UART, operator-control
  surface, and RAMPS header allocation to resolved; recorded StallGuard per-side
  tuning and external-driver caveats as the remaining motion items.
- **`components-and-references.md`:** TMC2209 `PDN_UART`/`DIAG` confirmed exposed,
  UART mode committed (`TMCStepper`); added LED / beeper / button / latched-E-stop
  hardware to the inventory.

## v0.9.1

- **Program editor tab implemented.** New "Program" tab in `pnp_gui.py`
  (`tab_program.py`): waypoint-based editing that generates MOVE / PROBE_Z
  sequences from a table, with local validation, upload, and retrieval of the
  stored program. Previously listed as planned.
- **Chunked `load_program` transfer implemented.** GUI and simulator now support
  the `begin_transfer` / `program_chunk` / `end_transfer` sequence for programs
  over 200 bytes (`gui_worker.py`, `simulator.py`). `communication-protocol.md`
  §4.3 rewritten to document the as-built wire format and its NACK reasons.
- **`gui_worker.py`:** extracted the chunk-vs-direct decision into
  `_should_chunk()` with a named `MAX_DIRECT_PAYLOAD_BYTES = 200` constant.
- **Unit test suite added** under `Software/tests/` (pytest): coverage for the
  interpreter, the simulator state machine / protocol / chunked transfer, and the
  serial-worker framing logic. Runs without hardware or PyQt6/pyserial.
- **Documentation consistency pass.** Corrected the §4.3 chunk format and reason
  codes in `communication-protocol.md`; marked `JUMP`/`LABEL` as reserved / not
  yet implemented in `job-program.md` and fixed the loop-limit wording; updated
  stale "not implemented" status in `README.md`; updated the GUI tab list and
  fixed section ordering in `architecture.md`; replaced retired-state references
  (`DEPOSITING` / `PLACING`) in `pin-mapping.md`.

## v0.9

- **Major software development phase — GUI, simulator, interpreter.**
- Reorganized from a single architecture document into separate focused files
  (`communication-protocol.md`, `job-program.md`, and the software files below).
- **Protocol v0.9:** Simplified state machine from 14 states to 7 (`IDLE`,
  `HOMING`, `READY`, `RUNNING`, `PAUSED`, `FAULTED`, `ESTOPPED`). Retired
  `start_job`; added `load_program`, `get_program`, `run_program`. Added
  executor status fields (`current_op`, `step_index`, `loop_iter`, `variables`)
  to the periodic status message. Added `x_mm`/`y_mm`/`z_mm` to status.
  Added `query_positions`, `move_to`, `save_position` commands. Added
  `laser_interlock_mode` config param with expandable mode table.
- **`interpreter.py`:** Standalone Python program execution engine implementing
  the full instruction set from `job-program.md`. Supports MOVE, PROBE_Z, HOME,
  OUTPUT, READ_SENSOR, WAIT, DELAY, LOOP_WHILE, LOOP_FOR, IF, CALL/RETURN,
  SET_VAR, LOG, HALT, FAULT. Pause/resume via threading events. E-stop abort.
  Loop overflow (10,000 iter) and call depth (8 levels) safety limits.
  ProgramValidator for load-time checking. Reference implementation for the
  eventual C++ firmware port.
- **`simulator.py` (v0.3):** Fully rebuilt around the interpreter. TCP server
  on `localhost:9999`. 7-state safety layer wraps interpreter thread. Simulated
  motion with configurable speed and minimum move duration. Virtual surface
  heights for PROBE_Z simulation (`surface_home`, `surface_deposit` console
  commands). Console commands: `load`, `run`, `pause`, `resume`, `fault`,
  `estop`, `laser_home/busy`, `material on/off`, `surface_home/deposit`.
- **`pnp_gui.py` (v0.2):** PyQt6 GUI with four tabs: Run, Service, Comms,
  Events. Run tab: state banner, sensor indicators, program name/current op,
  Load Program / Run Program / Pause / Resume / E-Stop controls with correct
  enable/disable per state. Service tab: target position with per-axis inc/dec,
  named positions table with Teach Current / Teach Target, servo and output
  controls with state-aware button colors. Comms tab: ToF sensor table and
  communications log. Events tab: timestamped log of user actions, program steps,
  state transitions, and faults; filter by category; save as .txt or .csv.
- **`job-program.md`:** First-class instruction set specification covering
  program format, all instruction types, variables, conditions, subroutines,
  safety rules, EEPROM storage notes, and a full example program.
- **`demo_program.json`:** 3-cycle pick-and-place demonstration program
  exercising all major instruction types and subroutines. ~6 minutes runtime
  per run. Full cycle confirmed working end-to-end.
- **Architecture updates:** Vacuum release confirmed as solenoid (servo-valve
  option retired). Two servos added: door servo (SERVO2/D5) and laser button
  servo (SERVO3/D4). Servo assignments added to `pin-mapping.md`.
- **`open-decisions.md`:** Closed vacuum release and servo assignment decisions.

## v0.8

- Applied review fixes; split documentation into focused file set.
- Added VL53L0X 8-bit vs 7-bit address warning.
- Added homing/limit-switch and dual-Y gantry decisions as blocking items.
- Added persistent-config implementation notes.
- Initial GUI and simulator scaffolding.
