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

- Final stepper motor choice, and whether TMC2209 StepStick drivers are sufficient
  under real load or an external driver (TB6600 / DM542 / DM556) is needed.
- **Homing and travel limits.** Decide X/Y/Z home direction, limit-switch count,
  min/max vs home-only strategy, switch type (NC preferred), debounce/filtering,
  homing speeds, backoff distance, and software travel-limit behavior after homing.
- **Dual-Y gantry strategy.** Decide whether the two Y motors are always slaved
  together or can home independently to square the gantry. If independent squaring
  is required, reserve two Y home switches and define fault behavior if one side
  loses steps.
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
- Final hardware E-stop power-removal design (contactor / safety relay selection).
- Final RAMPS header assignment for Start / Pause / E-stop / interlocks / axis
  endstops. The current suggested Start/Pause/E-stop use endstop headers, which may
  conflict with axis limits unless additional headers or an I/O expansion plan is
  chosen.

## Protocol

- ~~Protocol v0.9 locked~~ **Resolved.** See `communication-protocol.md`.
- **Chunked `load_program` transfer.** The GUI currently sends the entire
  program JSON in a single TCP line (limit raised to 64 KB in simulator).
  Real Mega firmware needs proper chunked transfer as defined in
  `communication-protocol.md` §4.3. Implement before hardware bring-up.
- **Program editor tab.** GUI tab for writing, editing, and uploading job
  programs. Not yet implemented.
