# Open Decisions

Live questions that are not yet settled. Move an item into the relevant doc
(and delete it here) once it is locked.

---

## Resolved since last revision

- **Vacuum release** → solenoid valve confirmed. See `architecture.md` §6.
- **Servo assignments** → door servo SERVO2/D5, laser button SERVO3/D4.
- **Firmware state machine** → 8 states (IDLE, HOMING, READY, RUNNING, PAUSED,
  FAULTED, ESTOPPED, CALIBRATING). CALIBRATING added for automated steps/mm
  calibration. See `firmware-architecture.md`.
- **Job execution model** → program interpreter confirmed. Python reference
  implementation in `interpreter.py`; C++ port complete and host-tested.
- **GUI framework** → PyQt6 + pyserial. Simulator over TCP socket.
- **Stall / jam / homing detection** → TMC2209 UART + StallGuard4. All four
  drivers on one single-wire UART bus; DIAG interrupt provides sensorless homing
  and in-motion jam detection. See `architecture.md` §7, §11.
- **Homing strategy** → sensorless via StallGuard (drive into hard stop, detect
  stall). Removes physical endstops; software travel limits after homing.
- **Dual-Y gantry** → independent squaring. Y2 on E0 socket; Y1/Y2 home to
  separate DIAG lines.
- **Operator control surface** → latched E-stop + Start/Pause + RGB LED +
  program-loaded LED + beeper. Button semantics in `architecture.md` §9.1.
- **Machine geometry** → Cartesian gantry (X / dual-Y / Z) confirmed.
- **Chunked transfer (C++ firmware)** → implemented. `begin_transfer` /
  `program_chunk` / `end_transfer` fully ported and host-tested (32 SM tests).
- **Pump driver** → relay module on D32 (AUX-4 pin 3). AEDIKO 12V 1-ch
  optocoupler relay, HIGH-level trigger. Flyback diode across pump load.
  See `pin-mapping.md`.
- **Persistent config architecture** → Mega EEPROM. `Config` struct with
  `version` byte + CRC16. Load at boot, save after calibration. `#ifdef ARDUINO`
  guards on EEPROM I/O keep CRC logic host-testable. Implementation pending.
- **Steps/mm calibration mechanism** → automated traverse.
- **ToF sensor offset calibration mechanism** → GUI `calibrate_sensors` command.
  Operator presses a flat surface against all 4 arm pickup holes; firmware reads
  ch0–ch3 and stores as baselines. Clearance = live − baseline; clearance ≈ 0
  → touching. GUI `Sensor Cal` tab shows live/baseline/clearance/status per
  channel. `tofOffsetMm[4]` must be added to Config struct for EEPROM persistence. Firmware homes axis,
  drives to far hard stop via StallGuard, counts raw steps. User supplies actual
  travel distance via GUI. `steps_per_mm = raw_steps / distance_mm`. No need to
  know belt pitch, pulley count, or microstepping. See `firmware-architecture.md`.
- **Protocol version** → v1.0. See `communication-protocol.md`.
- **RAMPS header assignment** → resolved. See `pin-mapping.md`.

---

## Blocking / high-impact

*(none currently)*

---

## Firmware

- **Config/EEPROM implementation** — architecture decided (see resolved above),
  code not yet written. Last host-testable firmware piece before bench work.
  Includes: `steps_per_mm[3]`, `tof_offsets[4]`, servo angles, probe params,
  CRC16, schema version, safe-defaults on invalid CRC. Also stores the loaded job program.

---

## Sensors / I2C

- **VL53L0X voltage path.** Confirm whether modules on hand are bare (2.8 V,
  need level translation) or breakout boards with onboard regulator (accept 5 V).
  See `components-and-references.md` §3.
- **TCA9548A pull-up wiring (if bare modules).** Select per-channel pull-up
  values using the datasheet Rp(min)/Rp(max) equations vs six-channel bus
  capacitance.
- **ToF crosstalk compensation.** VL53L0X has a built-in crosstalk compensation
  routine for covered glass — needs to be run once per sensor on first install.
  Distinct from the offset/baseline calibration (now resolved — see below).

---

## Motion / drivers

- **StallGuard tuning (per axis, per Y side).** `SGTHRS`/`TCOOLTHRS` must be
  tuned on hardware. Y1 and Y2 may need independent thresholds; mismatched
  sensitivity causes the gantry to square crooked. Validate on the bench.
- **Y-axis squaring verification.** After homing both Y sides, confirm the
  gantry beam is physically square (measure diagonals). Document the squaring
  trim procedure.
- **TMC2209 thermal headroom.** Confirm the StepStick modules hold up at
  ~2.5 A/phase under load. If not, external driver (TB6600/DM542) — but those
  lose StallGuard and require reworking homing.
- **Homing details.** Per-axis home direction, homing speed, backoff distance,
  and whether to fit optional hard-limit switches as a backstop.
- **12 V vs 24 V motor rail.**

---

## Vacuum / actuators

- **Pump datasheet** — model, voltage, continuous current, startup/stall current.
  Most important missing reference.
- **Pump speed control** vs simple ON/OFF.
- **Servo angle calibration** — door open/closed angles, laser-button press/
  release angles. Must be measured and stored in Config.

---

## Calibration (new)

- **Steps/mm verification procedure.** After automated calibration, command a
  known distance (e.g. 100 mm) and measure actual travel. Document acceptable
  error tolerance and the correction workflow.
- **Re-calibration policy.** Under what conditions should the operator re-run
  calibration? (Belt replacement, pulley change, after any motion fault during
  homing.) Document in operator guide.
- **ToF calibration persistence.** ToF offset and crosstalk values per sensor
  channel need to live in Config alongside `steps_per_mm`. Add to Config schema
  before finalising.

---

## Operator interface / system

- Whether the SSD1309 OLED is in the final operator panel.
- Whether thermistors are needed.
- Whether RS-485 is ever needed beyond USB serial.

---

## Safety / laser

- **Laser fault naming.** `laser_interlock` vs `laser_not_parked` — settle one
  canonical reason per condition before SafetyMonitor is implemented.
- **Hardware E-stop power-removal design.** Contactor / safety relay selection.
  The latched button + firmware ESTOPPED state is not the primary safety barrier.
- **Laser status input.** Dry-contact safe/ready signal, ToF-only confirmation,
  or user-mediated workflow?

---

## Protocol

- **Multi-sheet odd-batch limitation.** If a loop picks up more than one sheet
  and the total count is odd, the final iteration fails silently. Resolution
  requires pickup verification (READ_SENSOR after each pickup). Defer until
  hardware validation informs threshold values.
- **Continuous safety monitors not yet in simulator.** Laser-park interlock and
  pickup-loss detection documented in `architecture.md` §11 but not implemented
  in `simulator.py`. Implement in firmware before hardware bring-up.
- **Partial-transfer timeout.** Simulator holds a partial transfer buffer
  indefinitely; firmware should discard abandoned transfers after a timeout.
