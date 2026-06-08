# Open Decisions

Live questions that are not yet settled. Move an item into the relevant doc
(and delete it here) once it is locked.

---

## Resolved since last revision

- **Vacuum release** → solenoid valve confirmed. See `architecture.md` §6.
- **Servo assignments** → door servo SERVO2/D5, laser button SERVO3/D4.
  See `architecture.md` §6.1 and `pin-mapping.md`.
- **Firmware state machine** → simplified to 7 states (IDLE, HOMING, READY,
  RUNNING, PAUSED, FAULTED, ESTOPPED). Detailed mid-job states moved into
  job programs. See `communication-protocol.md` §2.
- **Job execution model** → program interpreter confirmed. Python reference
  implementation in `interpreter.py`; C++ port deferred to hardware phase.
- **GUI framework** → PyQt6 + pyserial. Simulator over TCP socket.
- **Stall / jam / homing detection** → **TMC2209 UART + StallGuard4 (now, not
  deferred).** All four drivers on one single-wire UART bus (MS1/MS2 addressing);
  `DIAG` → interrupt provides sensorless homing and in-motion jam detection
  (`motion_fault`). `PDN_UART` and `DIAG` confirmed exposed on the modules in hand.
  See `architecture.md` §7, §11 and `pin-mapping.md` §2–§3.
- **Homing strategy** → **sensorless via StallGuard** (drive into a hard stop,
  detect the stall). Removes physical min-endstops and frees the endstop headers for
  DIAG + E-stop. Software travel limits after homing; optional hard limit switches as
  a backstop. See `architecture.md` §7.
- **Dual-Y gantry** → **independent squaring.** Y2 on the E0 socket with its own
  STEP/DIR; Y1 and Y2 home to separate `DIAG` lines so each side squares to its own
  stall. Chosen for gantry accuracy over the simpler slaved option; cost is more
  firmware and per-side StallGuard tuning. See `architecture.md` §2, §7.
- **Operator control surface** → latched E-stop + Start/Pause buttons + RGB status
  LED + program-loaded LED + beeper, forming a complete headless interface. Button
  semantics in `architecture.md` §9.1; pins in `pin-mapping.md` §3–§4.

---

## Blocking / high-impact

- **Cartesian gantry vs. articulated arm.** The architecture and pin tables assume a
  Cartesian X / dual-Y / Z gantry. If it is actually an articulated or SCARA arm,
  the motion math, homing, and the GUI jog/teach interface all change. This affects
  both firmware and GUI, so resolve early.

## Sensors / I2C

- **VL53L0X voltage path.** Confirm whether the modules on hand are bare (2.8 V,
  3.6 V-max signal pins — need translation) or breakout boards with an onboard
  regulator + level shifter (accept 5 V). See `components-and-references.md` §3.
- **TCA9548A translation wiring (if bare modules).** Select VCC and per-channel
  pull-up resistor values using the datasheet's Vpass-vs-VCC curve and the
  Rp(min)/Rp(max) equations against six-channel bus capacitance. Not yet a settled
  value.

## Motion / drivers

- **StallGuard tuning (per axis, and per Y side).** `SGTHRS`/`TCOOLTHRS` must be
  tuned on hardware; the two Y sides need independent thresholds or the gantry can
  square crooked. Validate squareness and jam-trip reliability on the bench.
- Whether the TMC2209 StepSticks hold up thermally at ~2.5 A/phase under load, or an
  external driver (TB6600 / DM542 / DM556) is needed — note those lose StallGuard, so
  sensorless homing would have to be reworked.
- **Remaining homing details.** X/Y/Z home direction, homing speeds, backoff
  distance, and whether to fit optional hard-limit switches as a backstop behind the
  mechanical stops.
- 12 V vs 24 V motor rail.
- Whether to keep the L298N as a learning/bench module or drop it from the design.

## Vacuum / actuators

- **Pump model, voltage, continuous current, and startup/stall current** — pump
  datasheet still needed. Most important missing reference.
- Pump speed control vs simple ON/OFF.
- Vacuum release: servo valve, solenoid valve, or pump reversal.
- Pump/valve driver: relay vs MOSFET module vs **RAMPS MOSFET output**. The RAMPS
  output is now more plausible (logic-level STP55NF06L parts), pending a
  current-path / connector / fuse-rating check (~5 A / ~11 A polyfuses).
- Whether to add a vacuum pressure sensor in addition to ToF pickup verification.

## Operator interface / system

- Whether the SSD1309 OLED is in the final operator panel.
- Whether LCD/encoder manual input is needed.
- Whether thermistors are needed.
- Whether RS-485 is ever needed beyond USB serial.
- Final persistent-configuration storage method (Mega EEPROM vs GUI-loaded vs
  hybrid), including schema version, CRC/checksum, defaults, invalid-config
  behavior, and EEPROM wear rules.

## Safety / laser

- How to receive direct laser status (dry-contact safe/ready signal, ToF-only
  confirmation, user-mediated workflow with firmware lockout, or a combination).
- ~~Final RAMPS header assignment for Start / Pause / E-stop / interlocks / axis
  endstops.~~ **Resolved.** Sensorless homing freed the endstop headers: D3 = E-stop,
  D2/D18/D19 = the StallGuard DIAG lines (Y1, Y2, X+Z), buttons on spare GPIO, with
  D14/D15 left for optional hard limits. See `pin-mapping.md` §3–§4.
- Final hardware E-stop power-removal design (contactor / safety relay selection)
  still open — the latched button + firmware ESTOPPED is not the primary safety
  barrier.
- **Laser fault naming.** `communication-protocol.md` §6.2 lists both
  `laser_interlock` (interlock not met) and `laser_not_parked` (arm moved while
  the head is unparked), and `architecture.md` §11 uses `laser_not_parked`, but
  the simulator's injectable fault set currently only has `laser_interlock`.
  Settle one canonical reason per condition before the firmware implements the
  continuous laser-park monitor.

## Protocol

- ~~Protocol v0.9 locked~~ **Resolved.** See `communication-protocol.md`.
- ~~Program editor tab~~ **Resolved.** Waypoint-based editor implemented.
  Generates MOVE / PROBE_Z sequences from a table. Load, save, validate, upload.

- ~~**Chunked `load_program` transfer.**~~ **Resolved for GUI + simulator.**
  The GUI chunks programs over 200 bytes using the `begin_transfer` /
  `program_chunk` / `end_transfer` sequence (`communication-protocol.md` §4.3),
  and the simulator implements the receiving side. Two follow-ups remain for
  hardware: the C++ firmware must implement the same sequence, and a
  partial-transfer timeout is still needed (the simulator does not yet discard
  an abandoned transfer — it holds the buffer until the next `begin_transfer`).

- **Multi-sheet odd-batch limitation.** If a loop iteration picks up more than
  one sheet and the total batch count is odd, the final pickup attempt on the last
  iteration fails silently — the arm executes the waypoints with nothing grabbed.
  Resolution requires pickup verification: a READ_SENSOR step after each pickup
  that checks ch0–3 and branches on failure. Defer until hardware validation
  informs the threshold values and failure modes. For now, multi-sheet programs
  require even batch counts.

- **Continuous safety monitors not yet in simulator.** Two firmware-level
  safety checks are documented in `architecture.md` §11 but not yet implemented
  in `simulator.py`:
  1. **Laser park interlock** — fault `laser_not_parked` if arm moves while
     ToF ch4 reads out-of-range.
  2. **Pickup loss detection** — fault `pickup_lost` if pump ON + arm moving +
     all pickup ToF sensors (ch0–3) lose the object.
  These must be implemented in firmware before hardware bring-up. Simulator
  implementation is optional but would improve fault-injection test coverage.
