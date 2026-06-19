# Pin Mapping (RAMPS 1.4 + Arduino Mega 2560)

RAMPS 1.4 mappings for the current build. Mega-side facts are verified against the
official Mega 2560 pinout [ref: Mega pinout]. This reflects the **post-pivot**
hardware: **limit-switch homing** (StallGuard retired), **VL53L4CD ToF behind a
TCA9548A mux**, an **L298N H-bridge pump** and **AOD4184 MOSFET solenoid**, and
**jog-and-measure** steps/mm calibration. About 90% of this is wired on the bench;
verify against the physical board labels before permanent installation.

The four motors are **X, Y1, Y2, Z** with **independent dual-Y** (the two Y motors
are squared by homing each side to its own limit switch). Steps/mm is stored
**per motor** (Y1/Y2 independent); soft travel limits are stored **per axis**
(one Y envelope). See `architecture.md` §2/§7 and `firmware-architecture.md`.

---

## 1. Stepper axis pins

| Motor | Socket | STEP | DIR | ENABLE |
|---|---|---:|---:|---:|
| X  | X  | D54 / A0 | D55 / A1 | D38 |
| Y1 | Y  | D60 / A6 | D61 / A7 | D56 / A2 |
| Y2 | E0 | D26 | D28 | D24 |
| Z  | E1 | D36 | D34 | D30 |

**Socket map:** X→X, Y1→Y, **Y2→E0**, **Z→E1**. The **Z socket (D46/D48/D62) is
unused** and its pins are spare GPIO. Y2 is driven independently from the E0 socket
(its own STEP/DIR) so the gantry squares by homing each Y side to its own switch.

Notes: Mega analog pins A0–A15 are also addressable as digital D54–D69. RAMPS
stepper ENABLE is active-low (LOW = enabled).

## 2. TMC2209 UART

All four drivers run in **UART mode** so the firmware can set current/microstepping
in software. (StallGuard sensorless homing has been **dropped** in favour of limit
switches — see §3 — but UART is still used for current/microstep configuration.)
UART is a single half-duplex wire (`PDN_UART`) shared by all four drivers; each
driver gets a unique address via its **MS1/MS2 jumpers** (0–3), so the bus costs
one Mega pin and no microstep jumpers.

| Function | Pin | Header | Notes |
|---|---:|---|---|
| TMC UART (shared bus) | D40 | AUX-2 | single wire to all four `PDN_UART`; `SoftwareSerial` |

`PDN_UART` is hand-wired from the StepStick pads (RAMPS does not route it). The
`DIAG` pads are **no longer used** (StallGuard retired); the E0 DIAG test wire on
D41 is freed.

## 3. Homing — limit switches

Homing uses **four mechanical limit switches**, one per motor, driven into at
homing speed and **polled** (no interrupt needed — only E-stop needs that). NC
contacts with `INPUT_PULLUP` are preferred so a broken wire fails safe. The two Y
switches let the firmware square the gantry by stopping each Y side at its own
switch. Switches use the freed ToF-XSHUT GPIO block (the mux made per-sensor XSHUT
unnecessary — see §6).

| Function | Pin | Notes |
|---|---:|---|
| X limit  | D27 | NC, `INPUT_PULLUP`, polled |
| Y1 limit | D29 | NC, `INPUT_PULLUP`, polled |
| Y2 limit | D31 | NC, `INPUT_PULLUP`, polled |
| Z limit  | D33 | NC, `INPUT_PULLUP`, polled |
| *(spare)* | D35, D37 | remainder of the freed XSHUT block |

After homing to a switch (that position is 0), the usable envelope is
`[0, maxTravelMm]` per axis; a `MOVE` past it faults at runtime. The limits are
operator-entered and stored in Config (v4) — this replaces StallGuard's former role
of protecting the far end of travel. See `firmware-architecture.md`.

## 4. Buttons and E-stop (RAMPS endstop headers)

The RAMPS endstop block carries the two operator buttons and the E-stop. Use the
`S` and `-` pins only (not `+`); `INPUT_PULLUP`.

| Function | RAMPS header | Pin | Interrupt | Wiring |
|---|---|---:|---|---|
| Start button (green) | X_MIN | D3 | INT5 | momentary, S↔- |
| Pause button (red) | Y_MIN | D14 | — | momentary, S↔- (polled, debounced) |
| E-stop (latched, NC) | Z_MIN | D18 | INT3 | NC contact S↔-; see `architecture.md` §9.3 |
| *(spare)* | X_MAX / Y_MAX / Z_MAX | D2 / D15 / D19 | INT4 / — / INT2 | available |

**Button model:** Start = "proceed", Pause = "halt/dismiss" — context-dependent on
state (see `architecture.md` §9.1). Of the endstop pins, only E-stop needs the
hardware interrupt; the buttons are polled with debounce. (The green/red buttons
were labelled "dir A/B" during bench bring-up; their committed v1.0 roles are
Start/Pause.)

## 5. Actuators

### 5.1 Vacuum pump — L298N H-bridge

The 12 V vacuum pump is driven by an **L298N dual H-bridge** (bench-tested). ENA is
PWM-capable for speed control. The `5V Enable` jumper is installed; `Enable A`
removed so D11 drives ENA.

| Function | Pin | Header | L298N |
|---|---:|---|---|
| Pump IN1 | D23 | AUX-4 | IN1 |
| Pump IN2 | D25 | AUX-4 | IN2 |
| Pump ENA (PWM) | D11 | SERVO0 | ENA |

### 5.2 Solenoid valve — AOD4184 MOSFET

The 12 V solenoid is driven by an **AOD4184 MOSFET module** as a low-side switch
(bench-tested). Keep the flyback diode close to the solenoid terminals.

| Function | Pin | Header | AOD4184 |
|---|---:|---|---|
| Solenoid PWM | D6 | SERVO1 | PWM in |

### 5.3 Servos

| RAMPS servo | Pin | PWM? | Assignment |
|---|---:|---|---|
| SERVO2 | D5 | ✓ | **Door servo** — opens the gate so cut paper falls through during deposit (`servo_door`) |
| SERVO3 | D4 | ✓ | **Laser-button servo** — presses the laser cutter start button (`servo_laser_btn`) |

Servo power (red/ground) comes from an external regulated 5 V supply with its ground
tied to Mega/RAMPS ground; only the signal comes from the RAMPS servo header. (The
RAMPS `VCC→5V` jumper near the servo header was required to power the rail on the
bench.) Named angles live in Config and are GUI-adjustable.

> SERVO0 (D11) and SERVO1 (D6) are **repurposed** as the pump-ENA and solenoid PWM
> lines above — they are no longer free for status LEDs. The earlier RGB-status-LED
> plan on D6/D11 is dropped; see §6.

## 6. I2C and ToF array

- SDA = D20 (PD1), SCL = D21 (PD0) [ref: Mega pinout].
- The **TCA9548A mux** sits on the I2C/AUX header at address **0x70** (A0/A1/A2 to
  GND). Six **VL53L4CD** sensors all keep the default address **0x29**; the mux
  selects one channel at a time. `readDistanceMm(ch)` maps straight onto mux
  channels 0–5.
- Channel roles: **ch0–3** arm-pickup distance, **ch4** laser-head home, **ch5**
  remaining-material. (ch0–3 carry per-sensor offsets in Config.)
- **No XSHUT lines** are needed for addressing (the mux handles selection), which is
  what freed the D27–D37 block for the limit switches (§3).
- Use a **VL53L4CD** library (Pololu `vl53l4cd-arduino` or STM32duino) — **not** the
  VL53L0X library; the VL53L4CD uses a 16-bit register map and is not compatible
  [ref: VL53L4CD]. Validate short-range performance on the bench before assuming the
  1 mm floor (some setups read a ~70 mm floor until range-timing is configured).
- An optional OLED, if fitted, stays on the **main bus upstream** of the mux.

## 7. Status indicator and beeper

| Function | Pin | Header | Notes |
|---|---:|---|---|
| Beeper | D39 | AUX-4 | `tone()` |
| Heartbeat LED | D8 | RAMPS power-MOSFET output | ~1 Hz blink = "firmware alive"; drives an onboard RAMPS LED — **confirm which output LED is the green one on your board** |

The freed RAMPS power-MOSFET outputs (D8/D9/D10) are unused by the pump/solenoid
(those moved to the L298N/AOD4184), so D8 is used for the heartbeat LED and D9/D10
remain spare. (For v1.0 the only status light is this green heartbeat; the full RGB
status LED is deferred.)

## 8. Power MOSFET outputs (RAMPS)

The three logic-level STP55NF06L outputs [ref: RAMPS manual] are **all free** now
that the pump and solenoid use external driver modules.

| Pin | Use |
|---|---|
| D8  | Heartbeat LED (§7) |
| D9  | spare |
| D10 | spare |

## 9. Pin budget summary

| Resource | Used by |
|---|---|
| Stepper sockets X/Y/E0/E1 | X, Y1, Y2, Z (Z socket unused) |
| AUX-4 D23/D25 + SERVO0 D11 | L298N pump (IN1/IN2/ENA) |
| SERVO1 D6 | AOD4184 solenoid PWM |
| Servo headers D4/D5 | laser-button + door servos |
| Endstop headers D3/D14/D18 | Start / Pause / E-stop |
| Freed XSHUT block D27/D29/D31/D33 | X/Y1/Y2/Z limit switches |
| AUX-2 D40 | TMC UART |
| I2C D20/D21 | TCA9548A mux + ToF + optional OLED |
| AUX-4 D39 | beeper |
| RAMPS MOSFET D8 | heartbeat LED |
| Serial D0/D1 | USB PC link |

**Spare:** endstop headers D2/D15/D19, XSHUT-block remainder D35/D37, old DIAG-test
D41, RAMPS MOSFETs D9/D10, Z socket D46/D48/D62.

No resource is over-committed.
