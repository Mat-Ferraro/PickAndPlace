# Components and Reference Documentation

This file tracks the physical modules in the build, their datasheet-verified key
specs, and the reference-documentation library. Specs below are taken from the
datasheets currently held in the project; anything not yet confirmed is marked.

---

## 1. Module inventory

| Module / Part | Role | Status |
|---|---|---|
| Elegoo / Arduino Mega 2560 R3 | Controller | Bench-verified (upload, blink, serial, button, servo, solenoid) |
| RAMPS 1.4 shield | Expansion (stepper sockets, MOSFET outputs, endstops, servo headers, I2C breakout) | Installed |
| Teyleten Robot TMC2209 V2.0 StepStick | Stepper drivers (UART mode) | Installed; `PDN_UART` confirmed exposed (UART used for current/microstep config). StallGuard/`DIAG` no longer used — homing is by limit switches |
| 42BYGHW811 NEMA 17 stepper | Candidate axis motor | Candidate; ~2.5 A/phase (datasheet still needed) |
| MG90S-style micro servo | Door / laser-button actuators (×2) | Movement tested |
| L298N dual H-bridge | **Vacuum pump driver (committed)** — IN1 D23, IN2 D25, ENA D11/PWM | Bench-tested |
| AOD4184 MOSFET module | **Solenoid valve driver (committed)** — low-side switch, PWM D6 | Bench-tested |
| 4× mechanical limit switch | Homing (one per motor: X, Y1, Y2, Z); NC, polled | To wire (freed XSHUT block D27/29/31/33) |
| Hosyond 2.42" 128x64 SSD1309 OLED | Optional local status display | Optional (deferred for v1.0) |
| 6× VL53L4CD ToF sensor | Pickup verification + laser-home + material detection | New part (1 mm class); 1 of 6 wired |
| TCA9548A | I2C mux for the six identical VL53L4CD sensors (addr 0x70) | Bought; not yet wired |
| 3-wire mushroom E-stop button (latched) | Latched stop; release clears ESTOPPED → IDLE | Input tested (Z_MIN/D18) |
| Green heartbeat LED (RAMPS, D8) | "Firmware alive" ~1 Hz blink (full RGB status LED deferred) | On-board RAMPS LED |
| Piezo beeper | Headless audible status (press confirm, fault alert, refused-action chirp) | To source (D39) |
| 2× momentary pushbutton (Start, Pause) | Headless operator controls (D3 / D14) | Input tested |

---

## 2. Datasheet-verified specs

### 2.1 Arduino Mega 2560 [ref: Mega pinout]

- **I2C:** SDA = D20 (PD1), SCL = D21 (PD0).
- **External hardware-interrupt pins:** D2 (INT4), D3 (INT5), D18 (INT3),
  D19 (INT2), D20 (INT1), D21 (INT0). Since D20/D21 are committed to I2C, the
  freely usable interrupt pins are **D2, D3, D18, D19** — relevant for E-stop and
  endstops, which benefit from interrupt-capable inputs.
- **Current limits:** max **20 mA per I/O pin**, max **50 mA from the +3.3 V pin**.
  VIN accepts **6–20 V**. These are hard ceilings — never drive an actuator coil,
  pump, or motor from a GPIO or the 3.3 V rail.
- **PWM pins** include D2–D13 and D44–D46; the RAMPS servo headers (D11/D6/D5/D4)
  are all PWM-capable.

### 2.2 TCA9548A I2C multiplexer [ref: TCA9548A]

- **8-channel** bidirectional I2C switch. VCC operating range **1.65–5.5 V**,
  inputs **5 V-tolerant**.
- **Voltage translation:** the part is designed to bridge 1.8/2.5/3.3 V devices to
  a 5 V controller. VCC sets the maximum voltage passed downstream; external
  pull-ups set each bus's level. (See §3 below for how this applies to our 5 V
  Mega ↔ 3.3 V sensors case.)
- **Address:** set by A2/A1/A0, range **0x70 (all low) to 0x77 (all high)**.
  Default for this build: tie A2/A1/A0 low → **0x70**.
- **Control:** a single 8-bit register; write a bitmask where each bit enables one
  channel. Multiple channels can be enabled at once, but enabling **one at a time**
  is preferred for six identical sensors. Powers up with **all channels deselected**.
- **RESET:** active-low; connect to VCC through a pull-up if not driven by firmware.

### 2.3 VL53L4CD ToF sensor [ref: VL53L4CD]

- **Default I2C address 0x29** (7-bit). All six share it, which is why the
  TCA9548A mux is required.
- **Range:** up to ~1.3 m, **1 mm resolution / 1 mm-class minimum** — so no sensor
  recess is needed (unlike the old VL53L0X plan). *Validate close-range on the
  bench:* some setups read a ~70 mm floor until `SetRangeTiming` is configured.
- **Register map is 16-bit** — **not** compatible with VL53L0X/VL53L1X libraries.
  Use a dedicated VL53L4CD library: **Pololu `vl53l4cd-arduino`** (lean) or
  **STM32duino `VL53L4CD`** (ST ULD wrapper). Both expose `setAddress()` for
  multi-sensor setups, though here the mux handles selection.
- **Mux as level translator:** the TCA9548A (5 V-tolerant inputs) bridges the 5 V
  Mega bus to the sensor side, so it doubles as the translator regardless of whether
  the ZORZA breakouts are bare or regulated.
- **Calibration:** per-channel pickup offset stored in Config (`tofOffsetMm[4]`,
  ch0–3). No cover-glass crosstalk routine needed for bare sensors.

> The previous VL53L0X (DS11555) specs are superseded by this section. The old
> 2.8 V / 3.6 V-signal voltage concern is retired — the mux handles translation.

### 2.4 TMC2209 stepper driver [ref: TMC2209 datasheet]

- **Use mode:** **UART mode (committed)** — used to set current/microstepping in
  software and read status. STEP/DIR carries motion; a shared single-wire UART bus
  (MS1/MS2 addressing) configures the drivers. `PDN_UART` confirmed exposed.
  **Homing is by limit switches, so StallGuard4 / `DIAG` are not used.** Library:
  `TMCStepper`.
- **Current capability:** the TMC2209 chip is suitable for quiet two-phase stepper
  control, but the practical current limit depends heavily on the exact StepStick
  module, sense resistors, PCB copper, heatsink, airflow, and enclosure temperature.
- **For this build:** acceptable for unloaded/early bench testing. Because the
  selected 42BYGHW811 motor is about 2.5 A/phase, keep external TB6600 / DM542 /
  DM556-style drivers as an upgrade option if the StepSticks run hot, skip steps,
  or cannot provide enough torque.
- **Current-limit setup:** do not assume the default potentiometer setting is safe.
  The Vref/current formula must be taken from the exact StepStick module
  documentation because it depends on module design and sense resistor value.
- **Handling:** never install backwards, never insert/remove with power applied,
  never connect/disconnect motors while powered, and use cooling during sustained
  tests.

### 2.5 RAMPS 1.4 [ref: RAMPS manual]

- **Power MOSFETs (Q1–Q3): STP55NF06L**, logic-level N-channel (gate drivable
  directly from 5 V Arduino logic), ~60 V class, tens of amps. These are the
  high-current switched outputs (the heater/fan channels, D8/D9/D10 in typical
  firmware). Relevant to the pump-driver decision — see `open-decisions.md`.
- **Flyback diodes:** 1N4004 (D1, D2) are present on the board.
- **Resettable fuses:** MFR1100 (~11 A main) and MFR500 (~5 A) polyfuses gate the
  power inputs — this bounds how much current any RAMPS-switched output can carry.
- **Warning reproduced from the manual:** reversing input power or inserting stepper
  drivers incorrectly will destroy electronics and is a fire hazard. Insert/remove
  drivers and motors only with power off.

---

## 3. Design note: powering the VL53L4CD array through the TCA9548A

The six VL53L4CD sensors all default to address 0x29, so the TCA9548A mux (0x70)
provides per-sensor selection. The mux also handles level translation: its inputs
are 5 V-tolerant, so it bridges the 5 V Mega bus to the sensor side regardless of
whether the ZORZA breakouts are bare or carry an onboard regulator — one less thing
to verify. Run the mux VCC and the downstream pull-ups to suit the sensor side, keep
the upstream side compatible with the 5 V Mega bus, and select one channel at a time
via the mux control register. Confirm the exact pull-up values against the TCA9548A
datasheet's Rp(min)/Rp(max) equations versus the per-channel bus capacitance during
bring-up. An optional OLED, if fitted, stays on the main bus **upstream** of the mux.

---

## 4. Reference documentation index

### Held in project (datasheets on hand)

- Arduino Mega 2560 full pinout (`A000067-full-pinout.pdf`).
- RAMPS 1.4 manual / schematic (`RAMPS_1-4manual.pdf`).
- TCA9548A datasheet, TI (`tca9548a.pdf`).
- VL53L4CD datasheet + ULD user manual, ST (to add — supersedes the old VL53L0X DS11555).
- TMC2209 datasheet, Trinamic/ADI (`tmc2209_datasheet_rev1.09.pdf`).

### Source/purchase links captured (not a substitute for full datasheets)

- RAMPS 1.4 wiki: `https://reprap.org/wiki/RAMPS_1.4`
- 42BYGHW811 NEMA 17 product page: `https://protosupplies.com/product/stepper-motor-nema-17-2-5a-42byghw811/`
- TMC2209 StepStick pin config (Watterott): `https://learn.watterott.com/silentstepstick/pinconfig/tmc2209/`
- Relay module stable product/listing URL: `https://www.amazon.com/dp/B0B8HF14T2`
- L298N module stable product/listing URL: `https://www.amazon.com/dp/B07BK1QL5T`
- ZORZA VL53L4CD breakout product page: `https://www.amazon.com/dp/B0F1S78FWV`
- Hosyond SSD1309 OLED stable product/listing URL: `https://www.amazon.com/dp/B0G2RFLG1L`

These purchase links should still be replaced with saved PDFs/screenshots or
manufacturer docs when available.

### Still needed

- **Motion/drivers:** Teyleten V2.0 TMC2209 `PDN_UART` confirmed exposed; still
  needed are the module's Vref/current-limit formula and the 42BYGHW811 datasheet,
  plus a manual for any larger external driver (TB6600 / DM542 / DM556) if the build
  moves to one. Homing is by limit switches, so StallGuard is not a dependency.
- **Vacuum/actuators:** **vacuum pump datasheet — still the most important gap**;
  solenoid valve datasheet; L298N module datasheet; AOD4184 MOSFET module datasheet;
  MG90S / MG90S-SV01 servo datasheet.
- **Sensors:** VL53L4CD datasheet + ULD user manual; the four homing limit-switch
  part (contact rating, NC/NO).
- **Operator interface:** Hosyond SSD1309 OLED page + SSD1309 / U8g2 reference (if
  the optional display is fitted); beeper and Start/Pause button parts.
- **Power/safety:** 12 V / 24 V supply spec; external 5 V servo/sensor supply spec;
  fuse/breaker/E-stop contact-block/safety-relay datasheets once selected; wire,
  connector, and terminal-block ratings for actuator power.
- **Software libraries:** `TMCStepper` (UART), Servo, AccelStepper (or chosen motion
  lib), a **VL53L4CD** library (Pololu `vl53l4cd-arduino` or STM32duino — *not*
  VL53L0X), TCA9548A mux reference, SSD1309/U8g2/Adafruit_GFX, ArduinoJson.

### VL53L4CD API reference

The VL53L4CD is driven through ST's Ultra-Lite Driver (ULD) API; the Pololu and
STM32duino Arduino libraries both wrap it. Add the ULD user manual to the library —
the bare datasheet is not enough to configure range timing (relevant to the
close-range validation noted in §2.3). The old VL53L0X UM2039 reference no longer
applies.