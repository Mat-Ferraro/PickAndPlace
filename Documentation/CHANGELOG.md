
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
