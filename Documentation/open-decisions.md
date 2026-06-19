# Open Decisions

Live questions that are not yet settled. Move an item into the relevant doc
(and delete it here) once it is locked.

---

## Resolved since last revision

- **Vacuum release** → solenoid valve confirmed. See `architecture.md` §6.
- **Servo assignments** → door servo SERVO2/D5, laser button SERVO3/D4.
- **Firmware state machine** → 8 states (IDLE, HOMING, READY, RUNNING, PAUSED,
  FAULTED, ESTOPPED, CALIBRATING). CALIBRATING added for jog-and-measure steps/mm
  calibration. See `firmware-architecture.md`.
- **Job execution model** → program interpreter confirmed. Python reference
  implementation in `interpreter.py`; C++ port complete and host-tested.
- **GUI framework** → PyQt6 + pyserial. Simulator over TCP socket.
- **Stall / jam / homing detection** → **limit switches** for homing (StallGuard
  retired). TMC2209 UART is **kept** for current/microstepping config (single-wire
  bus, MS1/MS2 addressing); `DIAG`/StallGuard is no longer used.
- **Homing strategy** → **four mechanical limit switches** (one per motor, polled,
  NC + `INPUT_PULLUP`). Homing to a switch defines position 0; soft travel limits
  bound the far end. See `pin-mapping.md` §3.
- **Dual-Y gantry** → independent squaring. Y2 on E0 socket, Z on E1 socket; Y1/Y2
  home to **separate limit switches**.
- **Operator control surface** → latched E-stop + Start/Pause buttons + beeper +
  a single green **heartbeat LED** (RAMPS, D8) for v1.0; full RGB status LED
  deferred. Button semantics in `architecture.md` §9.1.
- **Machine geometry** → Cartesian gantry (X / dual-Y / Z) confirmed.
- **Chunked transfer (C++ firmware)** → implemented. `begin_transfer` /
  `program_chunk` / `end_transfer` fully ported and host-tested (32 SM tests).
- **Pump driver** → **L298N H-bridge** (IN1 D23, IN2 D25, ENA D11/PWM),
  bench-tested. **Solenoid driver** → **AOD4184 MOSFET module** (PWM D6),
  bench-tested. (Earlier relay-on-D32 / RAMPS-MOSFET options dropped.) See
  `pin-mapping.md` §5.
- **ToF sensors** → **6× VL53L4CD** (1 mm class, no recess) all at 0x29 behind a
  **TCA9548A mux at 0x70**, channels 0–5, dedicated VL53L4CD library (not VL53L0X).
  See `components-and-references.md`.
- **Persistent config architecture** → Mega EEPROM, `Config` struct **v4** with
  per-motor steps/mm, per-axis soft travel limits, ToF offsets, servo angles, probe
  params, CRC16. **Implemented and host-tested.**
- **Steps/mm calibration mechanism** → **jog-and-measure** (operator jogs a known
  step count via `cal_jog`, supplies measured mm; firmware computes
  `steps_per_mm = |net_steps| / mm`). Implemented and host-tested.
- **ToF sensor offset calibration mechanism** → GUI `calibrate_sensors` command.
  Operator presses a flat surface against the four arm pickup holes; firmware reads
  ch0–3 and stores baselines in `tofOffsetMm[4]` (Config v4). Clearance = live −
  baseline; clearance ≈ 0 → touching.
- **Soft travel limits** → per-axis `maxTravelMm` (operator-entered via
  `set_max_travel`, stored in Config v4), enforced on every `MOVE` by the
  interpreter; replaces StallGuard's far-end protection.
- **Protocol version** → v1.0. See `communication-protocol.md`.
- **RAMPS header assignment** → resolved. See `pin-mapping.md`.

---

## Blocking / high-impact

*(none currently)*

---

## Firmware

- **Real `Machine` HAL (bench).** `jogAxisSteps`, limit-switch homing (per-motor,
  per-Y-side squaring), VL53L4CD-through-mux reads, servos, pump (L298N), solenoid
  (AOD4184). Not host-tested; validated on hardware.
- **Python simulator + GUI calibration tab** still model the old auto-traverse;
  bring them into line with the firmware's jog-and-measure (`cal_jog`) before bench
  bring-up so the GUI and firmware agree.

---

## Sensors / I2C

- **VL53L4CD short-range validation.** Confirm on the bench that the sensors read
  reliably at close range; some setups floor at ~70 mm until range-timing is
  configured. The "1 mm, no recess" assumption rides on this.
- **VL53L4CD library choice.** Pololu `vl53l4cd-arduino` vs STM32duino — pick one on
  the bench (both wrap ST's part; 16-bit register map, **not** VL53L0X-compatible).
- **ToF channel-role confirmation.** ch0–3 arm-pickup distance, ch4 laser-head home,
  ch5 remaining-material — confirm against the final mechanical layout.

---

## Motion / drivers

- **Limit-switch homing details.** Per-axis home direction, homing speed, backoff
  distance, and debounce. NC + `INPUT_PULLUP`, polled.
- **Y-axis squaring verification.** After homing both Y sides to their switches,
  confirm the gantry beam is physically square (measure diagonals); document the
  squaring trim procedure.
- **TMC2209 current setup (UART).** Set per-motor current/microstepping over the
  shared UART bus; confirm the StepSticks hold up at ~2.5 A/phase under load. If not,
  external driver (TB6600/DM542) — still fine since homing no longer needs StallGuard.
- **12 V vs 24 V motor rail.**

---

## Vacuum / actuators

- **Pump datasheet** — model, voltage, continuous current, startup/stall current.
  Most important missing reference.
- **Pump speed control** vs simple ON/OFF.
- **Servo angle calibration** — door open/closed angles, laser-button press/
  release angles. Must be measured and stored in Config.

---

## Calibration

- **Steps/mm verification procedure.** After jog-and-measure calibration, command a
  known distance (e.g. 100 mm) and measure actual travel. Document acceptable error
  tolerance and the correction workflow.
- **Re-calibration policy.** When should the operator re-run calibration? (Belt
  replacement, pulley change, mechanical rework.) Document in the operator guide.
- **ToF calibration persistence** → resolved: `tofOffsetMm[4]` lives in Config v4.

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