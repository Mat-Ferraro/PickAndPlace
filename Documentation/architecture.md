# System Architecture

Working architecture for an Arduino Mega / RAMPS-based pick-and-place paper loader
that operates alongside, but outside the direct scope of, a laser cutter. The
machine moves uncut material into laser positions, verifies pickup, waits for
laser-safe conditions, retrieves cut material, and deposits finished parts. The
laser cutter is treated as an external system.

> Component-level specs cited here as `[ref: ...]` are detailed in
> `components-and-references.md`. Pin assignments live in `pin-mapping.md`.
> Unsettled questions live in `open-decisions.md`.

---

## 1. Hardware overview

- **Controller:** Elegoo / Arduino Mega 2560 R3. USB-B is used for programming,
  debug, and the primary PC link.
- **Expansion:** RAMPS 1.4 shield — stepper sockets, MOSFET outputs, endstop
  headers, servo headers, thermistor inputs, and I2C breakout. RAMPS covers most of
  the Mega headers, so external controls/actuators should use RAMPS headers where
  practical.

Hard electrical ceilings from the Mega [ref: Mega pinout]: 20 mA per I/O pin,
50 mA total from the 3.3 V pin, VIN 6–20 V. Nothing actuator-class is ever driven
from a GPIO, the 5 V pin, the 3.3 V pin, VIN, or USB.

## 2. Motion system

| Axis / function | Motors | Notes |
|---|---:|---|
| X | 1 | Single stepper |
| Y | 2 | Dual steppers (paired gantry drive) |
| Z | 1 | Single stepper |
| **Total** | **4** | Fits RAMPS X/Y/Z/E0 sockets |

The motor count and the pin tables assume a **Cartesian gantry**. If the machine is
actually an articulated/SCARA arm, this section and the jog/teach interface change
substantially — see `open-decisions.md`.

Candidate motor: 42BYGHW811 NEMA 17, 4-wire bipolar, 1.8°/step (200 steps/rev),
~2.5 A/phase. Because this is a relatively high-current NEMA 17, the StepStick
drivers are acceptable for early unloaded bench testing but may be marginal under
final load.

### Stepper drivers

Preferred: TMC2209 StepStick, run in **UART mode** (committed — not deferred). UART
lets the firmware set current/microstepping in software and read **StallGuard4**
load for sensorless homing and jam detection (see §7 and §11). All four drivers
share one single-wire UART bus, addressed individually via their MS1/MS2 jumpers.
Alternatives if the StepSticks run hot or lack torque at ~2.5 A/phase: external
TB6600 / DM542 / DM556 (but those lose StallGuard, so sensorless homing would need
rethinking).

The two Y motors (Y1, Y2) are driven **independently** — Y2 from the E0 socket with
its own STEP/DIR — so the gantry is squared by homing each Y side to its own stall
rather than slaving them. See `open-decisions.md` and `pin-mapping.md`.

**Driver safety rules:** insert drivers only with USB and motor power disconnected;
verify orientation before power; align the motor-output side with the RAMPS labels
(`2B 2A 1A 1B`); never connect/disconnect motors with motor power applied; set the
current limit before sustained motion; add cooling if drivers or motors run hot.

## 3. Power architecture

- **Logic:** USB-B powers the Mega for programming/debug. USB power is never used
  for motors, pumps, solenoids, or other actuators.
- **Motors:** powered from an external supply through the RAMPS motor-power input.
  Bench target 12 V DC, current sized to the motors under test. Final machine: 12 V
  or 24 V, chosen on driver capability, motion needs, thermal behavior, and actuator
  selection.
- **Servo:** signal from a RAMPS servo header, but power (red/ground) from an
  external regulated 5 V supply, with that supply's ground tied to Mega/RAMPS ground.
- **Pump / solenoid:** separate driver circuit (MOSFET, relay, or appropriate
  module), with flyback/transient suppression on any inductive load. Actuator current
  stays off the GPIO pins. Pump voltage/current must be confirmed (datasheet still
  needed).
- **Common ground:** Mega/RAMPS, driver logic, servo supply, pump/solenoid driver,
  and sensor grounds must share a reference.

## 4. Sensors

### 4.1 ToF array (6× VL53L0X)

| Sensor | Purpose |
|---|---|
| TOF-1..4 | Pickup verification, one per pickup corner |
| TOF-5 | Laser-head home verification |
| TOF-6 | Remaining-material detection |

All six VL53L0X share the same default I2C address. The datasheet expresses it as
**0x52** in 8-bit notation, while Arduino `Wire` code normally uses **0x29** as the
7-bit address [ref: VL53L0X]. The **TCA9548A mux is required** to address the
sensors individually. The mux approach is preferred over XSHUT address-reassignment
for six identical sensors.

**Voltage:** the bare VL53L0X is a ~2.8 V part with **3.6 V-max signal pins — not
5 V-tolerant** [ref: VL53L0X]. Either use breakout boards that include a regulator
and level shifter, or use the TCA9548A as the level translator (it is 5 V-tolerant
and translation-capable [ref: TCA9548A]). The exact translation wiring is an open
item — see `components-and-references.md` §3 and `open-decisions.md`.

### 4.2 TCA9548A mux topology

```
Mega/RAMPS I2C (5 V) ── TCA9548A (addr 0x70)
                          ├─ Ch0 → TOF-1
                          ├─ Ch1 → TOF-2
                          ├─ Ch2 → TOF-3
                          ├─ Ch3 → TOF-4
                          ├─ Ch4 → TOF-5
                          ├─ Ch5 → TOF-6
                          └─ Ch6,7 → spare
```

Enable one channel at a time via the single 8-bit control register; tie A2/A1/A0
low for address 0x70; pull RESET high (or drive it from firmware) [ref: TCA9548A].

### 4.3 Optional thermistors

Optional inputs for RAMPS board, driver-area, enclosure, or motor temperature.
Not needed for early bring-up but retained as a possible reliability diagnostic.

## 5. Local display (optional)

Hosyond 2.42" 128×64 SSD1309 I2C OLED for IDLE/RUNNING/FAULT/ESTOPPED status. Not
required for headless operation and **not a safety interface**. It should stay on
the upstream/main I2C bus (the mux exists to isolate the identical ToF sensors, not
the display), unless an address conflict forces otherwise.

## 6. Vacuum system

One vacuum pump plus one vacuum-release mechanism.

| Release option | Status |
|---|---|
| MG90S-style servo valve | Servo movement tested; simple if the valve linkage is acceptable |
| Solenoid valve | Open; needs a driver + flyback protection |

Pickup is verified by the four pickup ToF sensors rather than vacuum pressure alone
(a pressure sensor could be added later).

**Driver candidates:** a 12 V relay (acceptable for early ON/OFF testing; verify
trigger voltage, contact rating, current, add suppression); a logic-level MOSFET
module (preferred for a DC pump if within rating, supports PWM); the L298N (bench
experiments only, not preferred); or a **RAMPS MOSFET output** — now more plausible
since the RAMPS power MOSFETs are logic-level STP55NF06L parts [ref: RAMPS manual],
pending a current-path/connector/fuse check (RAMPS outputs are gated by ~5 A/~11 A
polyfuses). See `open-decisions.md`.

## 7. Homing and travel limits

**Homing is sensorless**, using TMC2209 StallGuard4: each axis is driven into a
solid mechanical hard stop and the resulting motor stall (reported on the driver's
`DIAG` pin) is taken as the home reference. This removes the physical min-endstop
switches and frees the RAMPS endstop headers to carry the `DIAG` lines and the
E-stop (see `pin-mapping.md` §3).

- **Sequence:** axes home one at a time, so X and Z can share a single OR'd `DIAG`
  line. The two Y motors home **independently** to square the gantry — each Y side
  is driven to its own stall on a **separate** `DIAG` line, so the firmware stops
  each side individually and corrects racking on every home.
- **Travel limits:** after homing, software travel limits bound commanded motion; a
  `MOVE` outside the envelope faults at runtime. Optional hard limit switches on the
  spare Y_MIN/Y_MAX headers can back the mechanical stops.
- **Tuning:** StallGuard sensitivity (`SGTHRS`) is tuned per axis, and **per Y side**
  for squaring — differing friction/load between the two sides can square the gantry
  crooked, so each side's threshold is calibrated and squareness validated on
  hardware. StallGuard needs a minimum velocity (`TCOOLTHRS`) to read load, so it
  protects transit moves but not very slow ones (those rely on ToF, e.g. `PROBE_Z`).

Remaining open items (home direction/speeds, backoff, whether to fit the optional
hard-limit switches) are tracked in `open-decisions.md`.

## 8. Machine positions and states

Named positions: Home/Uncut Paper, Laser Position A, Laser Position B, Deposit
Finished Parts. Positions should be teachable from the GUI and stored persistently.

States: `IDLE, HOMING, READY, RUNNING, PAUSED, FAULTED, ESTOPPED`.

The detailed mid-job states (PICKING, VERIFY_PICKUP, MOVING_TO_LASER, etc.) that
appeared in earlier revisions have been retired. That sequencing is now expressed
inside the **job program** (see §15 and `job-program.md`) and reported to the GUI
via `current_op` in the status message.

State-machine rules: E-stop is checked continuously; the safety layer runs
independently of the program executor and cannot be suppressed by any program
instruction; GUI commands are requests that firmware state rules may accept or NACK
with a reason. See §11 for the two continuous safety monitors (laser park interlock
and pickup loss detection) that trigger FAULTED regardless of program state.

## 9. Physical controls and safety

Three physical controls — a latched **E-stop** plus **Start** and **Pause**
buttons — together with external status LEDs and a beeper form a complete headless
control surface (the machine can be operated with no GUI attached). Pin assignments
are in `pin-mapping.md` §3–§4.

### 9.1 Headless control model

The two momentary buttons are **context-dependent**: their meaning depends on the
current state, which is why the status indicators (§9.2) are functionally required,
not cosmetic — the operator must be able to read the state to know what a press will
do. The two-verb mental model is **Start = "proceed"**, **Pause = "halt / dismiss"**.

| State | Start | Pause |
|---|---|---|
| IDLE | `home` → HOMING | — |
| HOMING | — | — |
| READY | `run_program` *(only if a valid program is loaded)* → RUNNING | — |
| RUNNING | — | `pause` → PAUSED |
| PAUSED | `resume` → RUNNING | — |
| FAULTED | — | `reset_fault` → IDLE |
| ESTOPPED | — | — *(cleared by releasing the latched E-stop)* |

Both recovery exits — clearing a fault and releasing E-stop — land in **IDLE**,
which is inert and requires a deliberate Start to home before anything moves. So a
reflexive Pause that clears a fault cannot cause motion; worst case the operator
re-presses Start. No hold-to-confirm is needed for this reason.

**Implementation note:** the button handler synthesises the *same internal commands*
the serial parser produces (`home`, `run_program`, `pause`, `resume`, `reset_fault`).
There is one state machine with two input sources (serial and physical), so the
transitions are identical whether triggered by the GUI or a button — and they are
already exercised by the simulator's state-transition tests.

To start a stored program headless: power on → IDLE (no motion) → press Start to
home → READY → press Start to run. A program can only be *loaded* over USB; the
buttons can only *run* the program already stored in EEPROM. "Set up over USB once,
then run repeatedly headless" is the intended workflow (see §10, §12). Whether a
valid program is present is reported by `program_loaded` in the status message
(`communication-protocol.md` §7.1); Start in READY with no valid program is refused
with an error indication rather than running.

### 9.2 Status indicators

The onboard Mega LED is hidden under the shield, so status is shown on external
indicators driven from GPIO:

- **RGB status LED** — machine state by colour, with blink reserved for
  attention/transient states (e.g. green = READY, green slow-blink = READY-but-no-
  program, green fast-blink/blue = RUNNING, amber-blink = PAUSED, red-blink =
  FAULTED, red-solid = ESTOPPED, white-blink = HOMING).
- **Program-loaded LED** — solid when a valid program is in EEPROM.
- **Beeper** — confirms button presses (especially that a press registered),
  chirps when an action is refused (Start with no program), and is the primary
  attention-getter on a fault when no one is watching the LEDs.

### 9.3 E-stop

E-stop is a **hardware-latched** button (NC, fail-safe — a broken wire reads as
E-stop). When latched the firmware enters `ESTOPPED`; physically releasing the latch
returns the machine to `IDLE` (it does not auto-resume — position trust is lost, so
a re-home is required). **Software is not the primary safety mechanism.** The final
design must include a hardware-enforced circuit that removes power/enable from
hazardous actuators independent of firmware; the software state is for diagnostics,
handling, and reporting. E-stop is wired to a Mega hardware-interrupt pin
(D2/D3/D18/D19 [ref: Mega pinout]); the assignment is X_MIN/D3 (INT5).

### 9.4 Laser interlock

The machine must not enter the laser workspace unless the laser head is confirmed
parked/home (TOF-5), the laser is not operating, and the state machine permits it.
How to receive direct laser status is an open decision. Laser hazardous energy must
be controlled by the laser cutter's own safety system or a proper external
interlock — Arduino firmware is never the only barrier.

## 10. Headless operation

After a job starts the laptop may disconnect. The Mega continues executing, monitors
sensors, detects faults, and handles pause/fault/estop locally. The GUI is a setup,
command, monitoring, and service interface — **not** a real-time motion controller.
The firmware is the runtime authority.

## 11. Firmware architecture

Organize around two distinct layers:

**Safety layer (always running):**
State machine (`IDLE → HOMING → READY → RUNNING → PAUSED / FAULTED / ESTOPPED`),
E-stop monitoring, fault manager, command parser, status reporter, hardware
abstraction layer, calibration/config storage.

**Program executor (runs within RUNNING):**
Interpreter that reads a stored job program and executes it instruction by
instruction. The same instruction set runs in the Python simulator (`interpreter.py`)
and will be ported to C++ for the Mega. The executor is interruptible — E-stop and
hardware faults from the safety layer abort it immediately regardless of what
instruction is executing.

Rules: safety layer checks run between every executor instruction; E-stop is a
threading event that any blocking machine operation must honour; sensor polling stays
active headless; state transitions are logged/reported.

**Continuous safety monitors (run independently of program execution):**

1. **Laser park interlock.** ToF ch4 must read within the parked-threshold during
   any arm movement. If the laser head leaves its park position while the arm is
   moving (or before a move is started), abort motion and raise `laser_not_parked`.
   The arm must not move unless `laser_safe` is confirmed. This is a firmware-level
   check — it cannot be bypassed by a program instruction.

2. **Pickup loss detection.** While the pump is ON and the arm is moving, all four
   pickup ToF sensors (ch0–3) are polled. If none read below the grip-threshold
   (object no longer detected), raise `pickup_lost` and stop motion immediately.
   This catches workpieces dropped mid-transit and arm moves with nothing grabbed.

3. **Stall / jam detection (StallGuard).** During transit moves, the TMC2209
   `DIAG` lines flag a motor stall (jam, obstruction, or unexpected hard stop). A
   trip aborts motion and raises `motion_fault` with the offending axis. StallGuard
   needs a minimum velocity, so it covers transit moves but not very slow ones;
   slow precision moves (e.g. `PROBE_Z`) are covered by ToF instead. Steppers are
   open-loop, so this is the primary defence against silent lost steps — a software
   move-duration timeout cannot catch a jam, because step generation continues on
   schedule regardless of whether the motor turned.

All three faults transition the machine to FAULTED, halt the program executor, and
require an explicit `reset_fault` + `home` before resuming. None can be caught
or handled from within the job program.

## 12. Persistent configuration

Candidate stored config: home offsets, Laser Positions A/B, deposit position, pickup
sensor thresholds, remaining-material threshold, servo open/closed positions, motion
speed/accel limits, axis inversion flags, steps-per-mm, homing behavior.

For headless operation, critical calibration must live on the Mega (EEPROM) or be
loaded before the GUI disconnects. A hybrid split — critical calibration on the
Mega, job-specific data from the GUI — is the likely approach.

Persistent config should include a schema/version byte, CRC/checksum, factory
defaults, explicit save/load commands, invalid-config behavior, and EEPROM wear
protection. Do not auto-save every loop or on every streamed GUI edit.

## 13. System diagram


```
Windows GUI
   │  USB Serial / JSON
Arduino Mega 2560
   │
RAMPS 1.4
   ├─ TMC2209 drivers → X, Y1, Y2, Z steppers
   ├─ Endstop / button inputs
   ├─ Servo header (vacuum release)
   ├─ MOSFET outputs (pump / valve)  [logic-level STP55NF06L]
   └─ I2C (5 V)
        ├─ SSD1309 OLED (optional, upstream)
        └─ TCA9548A mux (0x70)
             └─ 6× VL53L0X (datasheet 0x52 / Arduino 0x29, one per channel)
```

## 14. GUI responsibilities

The GUI (`pnp_gui.py`) is a setup, command, monitoring, and service interface
implemented in PyQt6. It connects to either the Python simulator over TCP
(`socket://localhost:9999/`) or the Mega over a USB COM port. Five tabs:

- **Run:** state banner, sensor indicators (pickup, material, laser safe, e-stop),
  program name and current instruction, Load Program / Run Program / Pause / Resume
  / E-Stop / Reset controls with correct enable/disable per state.
- **Program:** waypoint-based job-program editor — build MOVE / PROBE_Z sequences
  from a table, validate locally, upload to the machine, and retrieve the stored
  program.
- **Service:** target position inc/dec controls (X/Y/Z), named positions table with
  Teach Current / Teach Target actions, servo and output test controls with
  state-aware button colours, raw input indicators.
- **Comms:** ToF sensor table, raw communications log (TX/RX JSON).
- **Events:** timestamped log of user actions, program steps, state transitions, and
  faults; filterable by category; saveable as .txt or .csv.

The GUI is built against the protocol in `communication-protocol.md`. The simulator
allows full GUI development and testing without hardware.

## 15. Software development stack

Three Python files form the development stack that allows GUI development and
end-to-end testing before any hardware is available.

```
pnp_gui.py          Windows operator interface
    │  JSON over TCP (socket://localhost:9999/)
simulator.py        Fake Arduino — speaks the protocol, enforces state machine
    │  calls
interpreter.py      Program execution engine — runs job JSON programs
    │  implements
MachineInterface    Abstract hardware layer (SimulatedMachine in simulator;
                    real drivers in firmware)
```

`interpreter.py` is the reference implementation of the program executor.
The C++ firmware port must match its behaviour exactly. Programs authored in
the GUI and validated by the Python interpreter will run unchanged on the Mega.
The Python test suite under `Software/tests/` exercises the interpreter,
simulator, and serial-worker layers and serves as a behavioural checklist for
the C++ port.
