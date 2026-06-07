
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
