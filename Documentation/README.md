# Pick-and-Place Paper Loader — Project Documentation

Version v1.0-dev (post-pivot: limit-switch homing, VL53L4CD ToF, jog-and-measure
calibration, Config v4 soft limits). The system loads uncut material into laser
positions, verifies pickup, waits for laser-safe conditions, and deposits finished
parts. The laser cutter is treated as an external system.

---

## Document set

| File | Purpose |
|---|---|
| `architecture.md` | Hardware overview, motion, power, sensors, states, safety. |
| `pin-mapping.md` | RAMPS 1.4 + Arduino Mega pin reference. |
| `communication-protocol.md` | JSON-over-USB command/status contract between GUI and firmware (v1.0). |
| `job-program.md` | Job program instruction set — the language the machine executes. |
| `components-and-references.md` | Module inventory with datasheet-verified specs and reference-doc index. |
| `open-decisions.md` | Live decision log — questions not yet settled. |
| `CHANGELOG.md` | Version history. |

## Software files

| File | Purpose |
|---|---|
| `pnp_gui.py` | Windows control panel (PyQt6). Connects to simulator or real hardware. |
| `simulator.py` | Fake Arduino — speaks the protocol over TCP so the GUI can be developed without hardware. |
| `interpreter.py` | Program execution engine — reads job programs and carries out instructions. Runs in the simulator now; ports to C++ firmware later. |
| `demo_program.json` | Full 3-cycle pick-and-place demo. Use this to exercise the simulator. |
| `test_program.json` | Minimal program for interpreter unit testing. |

---

## How to run

**Requirements:**
```
pip install PyQt6 pyserial
```

**Start the simulator (Terminal 1):**
```
python simulator.py
```

**Start the GUI (Terminal 2):**
```
python pnp_gui.py
```

The GUI connects to `socket://localhost:9999/` by default. Hit **Connect**.

**Run the unit tests:**
```
cd Software
pytest
```
No hardware, GUI, or PyQt6/pyserial install is required — the tests stub those
layers. They cover the interpreter, the simulator state machine and protocol,
and the serial-worker framing.

**Run the demo:**
1. Click **Home** on the Run tab — wait for READY (~3 s)
2. Click **Load Program...** — select `demo_program.json`
3. Click **Run Program** — watch the Events tab for phase-by-phase progress
4. Use simulator console commands to interact mid-run:
   - `pause` / `resume` — suspend and continue execution
   - `laser_busy` / `laser_home` — simulate the laser head moving away and back
   - `material off` — simulate the stack running out
   - `fault pickup_lost` — inject a hardware fault
   - `estop` / `estop_release` + `reset_estop` — E-stop cycle

**Simulator console commands:**

| Command | Effect |
|---|---|
| `load <path>` | Load a job program JSON file |
| `run` | Start the loaded program |
| `pause` / `resume` | Pause or resume |
| `fault <reason>` | Inject a hardware fault |
| `estop` / `estop_release` | Hardware e-stop cycle |
| `laser_home` / `laser_busy` | Toggle laser head position |
| `material on` / `off` | Toggle material presence |
| `surface_home <z>` | Set virtual stack surface height |
| `surface_deposit <z>` | Set virtual deposit pile height |
| `status` | Print machine state to console |

---

## Conventions

- **"Decided" vs "open."** Anything still under debate lives in `open-decisions.md`. Settled design lives in `architecture.md`. Move items out of the decision log when locked.
- **Datasheet facts are cited inline** as `[ref: <doc>]` pointing to `components-and-references.md`.
- **Pin numbers** follow the Arduino `Dnn` digital numbering; RAMPS header names given alongside.
- **Protocol** is the contract between GUI and firmware. Any change to message format or command set must be reflected in `communication-protocol.md` before implementation.
- **Programs** are JSON files following the instruction set in `job-program.md`. The Python interpreter is the reference implementation; the Mega C++ port must match it exactly.

---

## Current status

**Simulator and GUI: functional for development**

- Full protocol implemented: connect, home, load program, run, pause/resume, e-stop, fault injection.
- Chunked `load_program` transfer (`begin_transfer` / `program_chunk` / `end_transfer`) for programs over 200 bytes; see `communication-protocol.md` §4.3.
- Program interpreter executing all instruction types: MOVE, PROBE_Z, HOME, OUTPUT, READ_SENSOR, WAIT, DELAY, LOOP_FOR, LOOP_WHILE, IF, CALL/RETURN, SET_VAR, LOG, HALT, FAULT. (`JUMP`/`LABEL` are reserved, not yet implemented.)
- Program editor (Program tab): waypoint-based editing, local validation, upload, and retrieval of the stored program.
- Demo program runs 3 full pick-and-place cycles (~6 minutes) with realistic timing.
- Events tab logs all user actions, program steps, state transitions, and faults with save/export.
- Unit test suite (`Software/tests/`, pytest) covering the interpreter, simulator state machine / protocol / chunked transfer, and serial-worker framing.

**Not yet implemented in simulator:**
- Continuous safety monitors — laser-park interlock (`laser_not_parked`) and
  pickup-loss detection (`pickup_lost`), documented in `architecture.md` §11.
  Required in firmware before hardware bring-up. (In-motion stall/jam detection is
  no longer planned for v1.0 — StallGuard was retired with the move to limit-switch
  homing.)
- Headless control model (Start/Pause buttons, heartbeat LED, beeper) and the
  `program_loaded` flag — designed (`architecture.md` §9), not yet modelled.
- **Jog-and-measure calibration.** The firmware now uses `cal_jog` + `set_cal_distance`
  (and `set_max_travel` for soft limits); the simulator and GUI calibration tab still
  model the old auto-traverse and must be brought into line (next task).

**Not yet validated on hardware:**
- Stepper motion through RAMPS + TMC2209 in UART mode.
- Limit-switch homing and per-side dual-Y squaring.
- VL53L4CD wiring through the TCA9548A mux (1 of 6 sensors wired so far).
- Vacuum pump (L298N H-bridge) and solenoid (AOD4184 MOSFET) drivers.
- Hardware-enforced E-stop power removal.
- Headless operation: buttons, heartbeat LED, beeper.
- The real `Machine` HAL — `jogAxisSteps`, limit-switch homing, ToF-through-mux,
  servos. (Config v4, soft limits, and jog-and-measure logic are host-tested.)