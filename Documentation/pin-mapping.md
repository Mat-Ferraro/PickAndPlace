# Pin Mapping (RAMPS 1.4 + Arduino Mega 2560)

Typical RAMPS 1.4 mappings used by most Mega/RAMPS configurations. Mega-side facts
are verified against the official Mega 2560 pinout [ref: Mega pinout]. Final
axis/header assignments should be frozen after the mechanical layout is locked.

---

## 1. Stepper axis pins

| Axis | STEP | DIR | ENABLE |
|---|---:|---:|---:|
| X | D54 / A0 | D55 / A1 | D38 |
| Y | D60 / A6 | D61 / A7 | D56 / A2 |
| Z | D46 | D48 | D62 / A8 |
| E0 | D26 | D28 | D24 |
| E1 | D36 | D34 | D30 |

Notes: Mega analog pins A0–A15 are also addressable as digital D54–D69. RAMPS
stepper ENABLE is active-low.

## 2. Endstop / button inputs

| Header | Pin |
|---|---:|
| X_MIN | D3 | 
| X_MAX | D2 |
| Y_MIN | D14 |
| Y_MAX | D15 |
| Z_MIN | D18 |
| Z_MAX | D19 |

**Hardware-interrupt note [ref: Mega pinout]:** of the endstop pins, D2 (INT4),
D3 (INT5), D18 (INT3), and D19 (INT2) are external-interrupt-capable; D14/D15 are
not. For inputs that benefit from an interrupt (E-stop, critical endstops), prefer
D2/D3/D18/D19.

Dry-contact wiring: use the RAMPS endstop `S` and `-` pins (not `+`), configure
`INPUT_PULLUP`, prefer NC contacts for stop/interlock inputs.

### Suggested control allocation

This allocation is provisional. It is convenient for bring-up, but final motion may
need some of these endstop headers for actual axis homing/limits, especially if
dual-Y independent squaring is required.

| Function | RAMPS header | Pin | Interrupt? | Contact |
|---|---|---:|---|---|
| E-stop monitor | X_MIN | D3 | INT5 ✓ | NC preferred |
| Start button | X_MAX | D2 | INT4 ✓ | NO acceptable |
| Pause button | Y_MIN | D14 | no | NO/NC TBD |
| Spare interlock | Y_MAX | D15 | no | NC preferred |

## 3. Servo headers

| RAMPS servo | Pin | PWM? | Assignment |
|---|---:|---|---|
| SERVO0 | D11 | ✓ | Spare |
| SERVO1 | D6  | ✓ | Spare |
| SERVO2 | D5  | ✓ | **Door servo** — opens gate so cut paper falls through during `DEPOSITING` |
| SERVO3 | D4  | ✓ | **Laser button servo** — presses laser cutter start button at end of `PLACING` |

All four are PWM-capable [ref: Mega pinout]. Servo power (red/ground wires) comes
from an external regulated 5 V supply — not the RAMPS 5 V rail. The external 5 V
ground must be tied to Mega/RAMPS ground. Signal lines only come from the RAMPS
servo headers.

Named positions (`open`/`closed`, `press`/`release`) and their corresponding angles
are stored as EEPROM config params and are adjustable from the GUI without firmware
changes. See `architecture.md` §6.1 for sequence integration details.

## 4. I2C

- SDA = D20 (PD1), SCL = D21 (PD0) [ref: Mega pinout].
- RAMPS exposes I2C on its I2C/AUX header area. The TCA9548A connects here.
- D20/D21 are also INT1/INT0, but those interrupts are unavailable once the pins
  carry I2C.

## 5. RAMPS power MOSFET outputs

The three high-current switched outputs use logic-level STP55NF06L MOSFETs
[ref: RAMPS manual], typically mapped in firmware as D8, D9, D10 (heated-bed /
hotend / fan in printer firmware). These are candidates for the vacuum-pump driver
— see `open-decisions.md`. Outputs are gated by the board's ~5 A / ~11 A polyfuses.
