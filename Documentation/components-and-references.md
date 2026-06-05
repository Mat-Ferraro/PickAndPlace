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
| Teyleten Robot TMC2209 V2.0 StepStick | Stepper drivers | Installed, motion not yet tested; chip datasheet held, exact module pinout still needed |
| 42BYGHW811 NEMA 17 stepper | Candidate axis motor | Candidate; ~2.5 A/phase (datasheet still needed) |
| MG90S-style micro servo | Vacuum-release actuator candidate | Movement tested |
| AEDIKO 12 V 1-ch relay module | Pump/solenoid ON/OFF candidate | Candidate |
| L298N dual H-bridge | Bench DC-motor experiments only | Likely not in final design |
| Hosyond 2.42" 128x64 SSD1309 OLED | Optional local status display | Optional |
| 6× VL53L0X ToF sensor | Pickup verification + laser-home + material detection | Not yet wired |
| TCA9548A | I2C mux for the six identical VL53L0X sensors | Not yet wired |
| 3-wire mushroom E-stop button | Stop / E-stop monitor input | Input tested |

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

### 2.3 VL53L0X ToF sensor [ref: VL53L0X]

- **Supply (AVDD):** 2.6–3.5 V (typ 2.8 V). **Absolute max 3.6 V.** This is a
  ~2.8 V part — **not** a 5 V part.
- **I/O (IOVDD):** default API mode is **1.8 V logic** (1.6–1.9 V); a **2V8 mode**
  is programmable (2.6–3.5 V). **SCL/SDA/XSHUT/GPIO1 absolute max is 3.6 V** — the
  signal pins are **not** 5 V-tolerant. (This is the key fact resolving the old
  "sensor voltage note.")
- **I2C:** up to 400 kHz. The datasheet lists the default device address as
  **0x52** in 8-bit address notation; Arduino `Wire` libraries usually use the
  corresponding 7-bit address **0x29**. All six sensors share this same default,
  which is why the mux is required. Reference registers for a quick bring-up
  sanity check: 0xC0→0xEE, 0xC1→0xAA, 0xC2→0x10.
- **XSHUT:** active-low; must always be driven (pull-up if the host does not control
  it). Recommended XSHUT/GPIO1 pull-ups 10 kΩ; I2C pull-ups ~1.5–2 kΩ at 2.8 V/400 kHz.
- **GPIO1:** open-drain interrupt output (new-measurement-ready); leave unconnected
  if unused.
- **Timing/behavior:** tBOOT ≤ 1.2 ms; FoV 25°; range up to 2 m (default profile
  ~1.2 m). Ranging modes: single, continuous, timed.
- **Current:** HW standby 3–7 µA, SW standby 4–9 µA, active ranging ~19 mA avg
  (peak up to 40 mA). Operating temp −20 to 70 °C.
- **Calibration (host-stored, once at manufacturing):** reference-SPAD, temperature
  (repeat if temp changes >8 °C), offset (at 10 cm), and crosstalk (only if a cover
  glass is used). Per the API user manual UM2039.

### 2.4 TMC2209 stepper driver [ref: TMC2209 datasheet]

- **Use mode:** STEP/DIR standalone mode first; UART configuration/diagnostics can
  be added later after basic motion is validated.
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

## 3. Design note: powering the VL53L0X array through the TCA9548A

The bare VL53L0X is a 2.8 V part whose signal pins top out at 3.6 V, while the Mega
drives I2C at 5 V. Two viable paths:

1. **Breakout boards with onboard regulator + level shifter** (common GY-530 /
   "VL53L0XV2" style). If the modules already regulate and level-shift, they accept
   a 5 V supply and 5 V I2C directly. **Confirm this for the exact modules on hand**
   before connecting anything.
2. **Bare modules via the TCA9548A as the translator.** The mux is built for exactly
   this 5 V↔3.3 V case. Run the mux VCC and the downstream (sensor-side) pull-ups at
   3.3 V, keep the upstream side compatible with the 5 V Mega bus (the mux inputs are
   5 V-tolerant), and the part clamps each downstream channel to its pull-up voltage.

The exact VCC and pull-up resistor values for path 2 should be worked through using
the TCA9548A datasheet's design-requirements section (Vpass vs VCC curve, and the
Rp(min)/Rp(max) equations against the bus capacitance of six channels). Treat the
specific resistor selection as an open item, not a settled value — see
`open-decisions.md`.

---

## 4. Reference documentation index

### Held in project (datasheets on hand)

- Arduino Mega 2560 full pinout (`A000067-full-pinout.pdf`).
- RAMPS 1.4 manual / schematic (`RAMPS_1-4manual.pdf`).
- TCA9548A datasheet, TI (`tca9548a.pdf`).
- VL53L0X datasheet, ST DS11555 Rev 6 (`vl53l0x.pdf`).
- TMC2209 datasheet, Trinamic/ADI (`tmc2209_datasheet_rev1.09.pdf`).

### Source/purchase links captured (not a substitute for full datasheets)

- RAMPS 1.4 wiki: `https://reprap.org/wiki/RAMPS_1.4`
- 42BYGHW811 NEMA 17 product page: `https://protosupplies.com/product/stepper-motor-nema-17-2-5a-42byghw811/`
- TMC2209 StepStick pin config (Watterott): `https://learn.watterott.com/silentstepstick/pinconfig/tmc2209/`
- Relay module stable product/listing URL: `https://www.amazon.com/dp/B0B8HF14T2`
- L298N module stable product/listing URL: `https://www.amazon.com/dp/B07BK1QL5T`
- Hosyond SSD1309 OLED stable product/listing URL: `https://www.amazon.com/dp/B0G2RFLG1L`

These purchase links should still be replaced with saved PDFs/screenshots or
manufacturer docs when available.

### Still needed

- **Motion/drivers:** exact Teyleten V2.0 TMC2209 StepStick module pinout and
  Vref/current-limit formula; 42BYGHW811 datasheet; manual for any larger external
  driver (TB6600 / DM542 / DM556) if the build moves to one.
- **Vacuum/actuators:** **vacuum pump datasheet — still the most important gap**;
  solenoid valve datasheet (if used); AEDIKO relay module page; MOSFET driver
  module datasheet (if used); MG90S / MG90S-SV01 servo datasheet.
- **Operator interface:** Hosyond SSD1309 OLED page + SSD1309 / U8g2 reference.
- **Power/safety:** 12 V / 24 V supply spec; external 5 V servo/sensor supply spec;
  fuse/breaker/E-stop contact-block/safety-relay datasheets once selected; wire,
  connector, and terminal-block ratings for actuator power.
- **Software libraries:** Servo, AccelStepper (or chosen motion lib), a VL53L0X
  Arduino library, TCA9548A mux reference, SSD1309/U8g2/Adafruit_GFX, ArduinoJson.

### VL53L0X API reference

The VL53L0X API user manual **UM2039** is referenced throughout the datasheet for
calibration and ranging-mode control and should be added to the library; the bare
datasheet is not enough to drive the sensor.
