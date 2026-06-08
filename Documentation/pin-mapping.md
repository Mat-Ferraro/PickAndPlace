# Pin Mapping (RAMPS 1.4 + Arduino Mega 2560)

Typical RAMPS 1.4 mappings used by most Mega/RAMPS configurations. Mega-side facts
are verified against the official Mega 2560 pinout [ref: Mega pinout]. Final
axis/header assignments should be frozen after the mechanical layout is locked.

This layout assumes **TMC2209 drivers in UART mode with StallGuard sensorless
homing** (see `architecture.md` §7 and §11). Sensorless homing removes the need
for physical min-endstop switches, which frees the RAMPS endstop headers to carry
the StallGuard `DIAG` lines and the E-stop instead. The four motors are **X, Y1,
Y2, Z** with **independent dual-Y** (the two Y motors are squared independently —
see `open-decisions.md`).

---

## 1. Stepper axis pins

| Motor | Socket | STEP | DIR | ENABLE |
|---|---|---:|---:|---:|
| X | X | D54 / A0 | D55 / A1 | D38 |
| Y1 | Y | D60 / A6 | D61 / A7 | D56 / A2 |
| Z | Z | D46 | D48 | D62 / A8 |
| **Y2** | **E0** | D26 | D28 | D24 |
| *(spare)* | E1 | D36 | D34 | D30 |

**Y2 is driven independently** from the E0 socket (its own STEP/DIR), not slaved to
Y1, so the gantry can be squared by homing each Y side to its own stall. The E1
socket is unused; its pins (D36/D34/D30) are available as spare GPIO.

Notes: Mega analog pins A0–A15 are also addressable as digital D54–D69. RAMPS
stepper ENABLE is active-low.

## 2. TMC2209 UART and StallGuard

All four drivers run in **UART mode** so the firmware can set current/microstepping
and read StallGuard load. UART is a single half-duplex wire (`PDN_UART`) shared by
all four drivers on one bus; each driver is given a unique address via its **MS1/MS2
jumpers** (addresses 0–3), so the shared bus costs only one Mega pin and no
microstep jumpers are needed (microstepping is set over UART).

| Function | Pin | Header | Notes |
|---|---:|---|---|
| TMC UART (shared bus) | D40 | AUX-2 | single wire to all four `PDN_UART`; `SoftwareSerial` |

`PDN_UART` and `DIAG` are confirmed broken out on the modules in hand. RAMPS does
not route these from the driver sockets, so they are hand-wired from the StepStick
pads — the standard Marlin-on-RAMPS sensorless approach. If `SoftwareSerial` proves
unreliable at speed, fall back to a hardware UART by relocating a `DIAG` line and
freeing `Serial1` (D18/D19) or `Serial3` (D14/D15).

## 3. Endstop headers — E-stop and StallGuard DIAG

With sensorless homing, the endstop headers no longer carry min-limit switches.
They carry the E-stop and the StallGuard `DIAG` lines instead. Homing is done **one
axis at a time**, so X and Z can share a single OR'd `DIAG` line; the two Y `DIAG`
lines are kept **separate** because both Y motors move together during squaring and
the firmware must know which side stalled.

| Function | RAMPS header | Pin | Interrupt | Notes |
|---|---|---:|---|---|
| E-stop monitor (latched, NC) | X_MIN | D3 | INT5 | hardware-latched button; see `architecture.md` §9.3 |
| Y1 `DIAG` | X_MAX | D2 | INT4 | sensorless home + jam, Y1 side |
| Y2 `DIAG` | Z_MIN | D18 | INT3 | sensorless home + jam, Y2 side |
| X + Z `DIAG` (wire-OR'd) | Z_MAX | D19 | INT2 | home one axis at a time; identify via `SG_RESULT` over UART |
| Optional hard limits | Y_MIN / Y_MAX | D14 / D15 | no | mechanical end-of-travel backstop (recommended) |

**Hardware-interrupt note [ref: Mega pinout]:** of the endstop pins, D2 (INT4),
D3 (INT5), D18 (INT3), and D19 (INT2) are external-interrupt-capable; D14/D15 are
not. All four interrupt-capable headers are consumed by E-stop + the three DIAG
groups. Only E-stop strictly needs the interrupt; the DIAG lines may also be polled,
and during a run a stall is identified by reading `SG_RESULT` over UART.

Dry-contact wiring (E-stop, hard limits): use the RAMPS endstop `S` and `-` pins
(not `+`), configure `INPUT_PULLUP`, prefer NC contacts for stop/interlock inputs so
a broken wire fails safe. Sensorless homing requires solid mechanical hard stops for
the carriage to drive into.

## 4. Operator controls and status indicators

The onboard Mega LED is covered by the shield, so status is shown on external LEDs.
Buttons are polled with debounce (only E-stop needs an interrupt). The button
behaviour and LED/beeper meanings are specified in `architecture.md` §9.

| Function | Pin | Header | Notes |
|---|---:|---|---|
| Start button | A15 | T2 (thermistor in) | polled, debounced |
| Pause button | D42 | AUX-2 | polled, debounced |
| Status LED — Red | D11 | SERVO0 | hardware PWM |
| Status LED — Green | D6 | SERVO1 | hardware PWM |
| Status LED — Blue | D44 | AUX-2 | hardware PWM |
| Program-loaded LED | A13 | T0 (thermistor in) | digital out |
| Beeper | A14 | T1 (thermistor in) | `tone()` (any GPIO) |

These spare-pin choices are flexible — any free GPIO works for the buttons, beeper,
and program LED; the status LED channels should stay on PWM pins (D6/D11/D44) for
brightness/colour control. Thermistor inputs (A13–A15) and AUX-2 (D40/D42/D44) are
used here as general GPIO since no thermistors are fitted.

## 5. Servo headers

| RAMPS servo | Pin | PWM? | Assignment |
|---|---:|---|---|
| SERVO0 | D11 | ✓ | **Status LED — Red** (repurposed; see §4) |
| SERVO1 | D6  | ✓ | **Status LED — Green** (repurposed; see §4) |
| SERVO2 | D5  | ✓ | **Door servo** — opens the gate so cut paper falls through during the deposit sequence (`servo_door` output) |
| SERVO3 | D4  | ✓ | **Laser button servo** — presses the laser cutter start button during the laser sequence (`servo_laser_btn` output) |

All four are PWM-capable [ref: Mega pinout]. Servo power (red/ground wires) comes
from an external regulated 5 V supply — not the RAMPS 5 V rail. The external 5 V
ground must be tied to Mega/RAMPS ground. Signal lines only come from the RAMPS
servo headers.

Named positions (`open`/`closed`, `press`/`release`) and their corresponding angles
are stored as EEPROM config params and are adjustable from the GUI without firmware
changes. See `architecture.md` §6.1 for sequence integration details.

## 6. I2C

- SDA = D20 (PD1), SCL = D21 (PD0) [ref: Mega pinout].
- RAMPS exposes I2C on its I2C/AUX header area. The TCA9548A connects here.
- D20/D21 are also INT1/INT0, but those interrupts are unavailable once the pins
  carry I2C.

## 7. RAMPS power MOSFET outputs

The three high-current switched outputs use logic-level STP55NF06L MOSFETs
[ref: RAMPS manual], typically mapped in firmware as D8, D9, D10 (heated-bed /
hotend / fan in printer firmware). Outputs are gated by the board's ~5 A / ~11 A
polyfuses.

| Function | Pin | Notes |
|---|---:|---|
| Pump | D8 | driver choice still open — see `open-decisions.md` |
| Valve (solenoid) | D9 | driver choice still open |
| *(spare)* | D10 | |

The pin assignment is the control line; whether it drives the RAMPS MOSFET directly,
a relay coil, or an external MOSFET module is the still-open pump/valve driver
decision in `open-decisions.md`. Any inductive load needs flyback/transient
suppression.

## 8. Pin budget summary

| Resource | Used by |
|---|---|
| Stepper sockets X/Y/Z/E0 | X, Y1, Z, Y2 (E1 free) |
| Interrupt headers D2/D3/D18/D19 | E-stop + Y1/Y2/X+Z DIAG |
| Endstop headers D14/D15 | optional hard limits / spare |
| AUX-2 (D40, D42, D44) | UART, Pause, LED-Blue |
| Servo headers D4/D5/D6/D11 | servos (×2) + LED R/G |
| Thermistor inputs A13/A14/A15 | program LED, beeper, Start |
| MOSFETs D8/D9 (D10 spare) | pump, valve |
| I2C D20/D21 | ToF mux + optional OLED |
| Serial D0/D1 | USB PC link |

No resource is over-committed; the E1 socket, D10 MOSFET, D15 header, and AUX
analog/digital pins remain spare.
