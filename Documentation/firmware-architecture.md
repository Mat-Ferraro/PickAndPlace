# Firmware Architecture

**Status:** core logic + Config (v4) complete, host-tested. Real HAL pending (bench).
MCU firmware for the pick-and-place gantry — Arduino Mega 2560 + RAMPS 1.4.
Built in the Arduino IDE; portable logic unit-tested on the host with g++/Unity.

---

## Guiding idea: the firmware is a port, not a fresh design

The behaviour is largely specified and tested in Python — `interpreter.py`,
the simulator's `StateMachine`, `ProgramValidator`, and the protocol. The firmware
ports that logic to C++, and the Python tests translate directly into the host Unity
suite. **Exception:** the calibration mechanism was reworked **firmware-first**
(jog-and-measure replacing the old StallGuard auto-traverse), so on that one path the
C++ side currently leads and the Python simulator/GUI are being brought into line.

---

## The structural rule: logic depends only on `IMachine`

Every layer above the hardware depends only on the abstract `IMachine` interface.
Never on Arduino APIs directly. This is what allows the logic to compile and run
under host unit tests.

| Context | `IMachine` implementation |
|---|---|
| Host tests | `MockMachine` — records every call, returns scripted values |
| Comms-first bring-up | `StubMachine` — no-ops everything, returns safe defaults |
| Real hardware | `Machine` — TMC2209/AccelStepper/ToF/servos (bench-validated) |

**AVR adaptations from Python:**
- No exceptions — abortable operations return `OpResult` instead of raising
- No hot-path heap allocation — fixed buffers; `ProgramStore` buffers the
  raw JSON in `malloc`'d memory only during a transfer, freed immediately after parse
- Time injected (`tick(nowMs)`) rather than read — keeps transitions deterministic
- String literals in PROGMEM via `PNP_STREQ`/`PNP_SNPRINTF` macros

---

## Layout

```
Firmware/
  PnpFirmware/                  Arduino sketch (open in the IDE)
    PnpFirmware.ino             setup()/loop() — wires protocol + SM + stub HAL
    src/
      core/
        State.h                 8-state enum + bitmask helper
        StateMachine.h/.cpp     state machine + command gating       (DONE, tested)
        Interpreter.h/.cpp      job program executor + soft limits   (DONE, tested)
        ProgramValidator.h/.cpp program structure validator          (DONE, tested)
        ProgramStore.h/.cpp     chunked transfer + JSON storage      (DONE, tested)
      hal/
        IMachine.h              abstract HAL interface (jogAxisSteps, moveTo, ...)
        StubMachine.h           no-hardware impl (comms-first)
        Machine.h               TODO: real hardware (bench)
      protocol/
        Protocol.h/.cpp         JSON-over-serial (Arduino-only layer)
      config/
        Config.h/.cpp           EEPROM persistence, schema v4         (DONE, tested)
        TravelLimits.h          per-axis soft work envelope           (DONE, tested)
      safety/
        SafetyMonitor.h         TODO: laser-park / pickup-loss
      platform/
        Platform.h              PROGMEM macros + host millis() shim
  test/
    MockMachine.h               recording IMachine double
    test_state_machine.cpp      54 tests
    test_interpreter.cpp        77 tests (interpreter + validator)
    test_config.cpp             24 tests
    unity/                      vendored Unity (ThrowTheSwitch)
    Makefile                    `make` builds and runs all host tests
    ArduinoJson-7.3.1/          vendored ArduinoJson for host builds
  firmware-architecture.md      this file
```

---

## States

Eight states (up from the Python simulator's seven — `CALIBRATING` was added
for the jog-and-measure steps/mm calibration procedure):

| State | Description |
|---|---|
| `Idle` | Powered on, not homed. Position unknown. |
| `Homing` | Homing sequence running (driving each axis into its limit switch). |
| `Ready` | Homed. Awaiting command. |
| `Running` | Interpreter executing a job program. |
| `Paused` | Interpreter suspended. Resumable. |
| `Faulted` | Hardware or program fault. Operator intervention needed. |
| `Estopped` | E-stop active. All motion stopped. |
| `Calibrating` | Operator jogging a known step count (`cal_jog`), then awaiting `set_cal_distance`. |

---

## Stepper calibration system (jog-and-measure)

The gantry computes `steps_per_mm` without needing to know belt pitch, pulley
teeth, or microstepping. The operator drives a known number of steps and measures
the result:

1. `calibrate_axis X` — enters `Calibrating` and zeroes the jog accumulator. No
   motion happens on its own.
2. `cal_jog X <steps>` — `IMachine::jogAxisSteps()` moves the motor by a raw,
   uncalibrated step count (signed for direction); the firmware accumulates the net
   total, surfaced as `cal_steps` in status. Repeatable.
3. `set_cal_distance X 160.0` — `steps_per_mm = |net_steps| / 160.0`, written to
   `Config` and saved to EEPROM, then back to `Idle`.

This replaces the earlier StallGuard auto-traverse (retired with the move to
limit-switch homing). `IMachine` exposes `jogAxisSteps(axis, steps)` instead of the
old `traverseToStop()`; the real HAL implements it as raw step pulses on one motor.

### Soft travel limits

Per-axis maximum travel is operator-entered (`set_max_travel`) and stored in Config.
After homing to a limit switch (position 0), the usable envelope is
`[0, maxTravelMm]`; the `Interpreter` enforces it on every `MOVE` (waypoints and
final target) via a single `guardedMove()` chokepoint, faulting with
`soft_limit_<axis>` before the target reaches the machine. Limits are pushed into the
interpreter from Config at `run_program`; an unconfigured (all-zero) envelope is
treated as unbounded so existing motion is unaffected until limits are set. This is
what now protects the far end of travel (formerly StallGuard's job).

---

## Unit testing

The host test suite (`Firmware/test/`) compiles the portable `.cpp` files with
g++ against `MockMachine` + Unity — the same framework used at Acclaro. Arduino
IDE builds the same files for the Mega.

```
cd Firmware/test && make    # builds test_state_machine + test_interpreter, runs both
```

**Current totals: 155 tests, 0 failures.**

| Suite | Tests | Covers |
|---|---|---|
| `test_state_machine` | 54 | Command gating, homing, run/pause/estop, transfer, jog-and-measure calibration, soft-limit enforcement, `set_max_travel` |
| `test_interpreter` | 77 | All interpreter ops, validator, conditions, flow control, WAIT, abort, soft-limit MOVE faults |
| `test_config` | 24 | CRC, load/save round-trip, v4 schema, per-motor steps/mm, travel limits, ToF offsets |

What is **not** host-tested: the `Machine` HAL — "does the TMC2209 actually step"
is validated on the bench.

---

## SRAM budget (Arduino Mega, 8 KB total)

SRAM is the limiting resource on the Mega. Current usage:

| Item | Bytes | Notes |
|---|---|---|
| Global variables | ~2700 | After all optimisations |
| Stack headroom | ~5500 | Available for call frames |

Key optimisations applied:
- `ProgramStore::buf_` is `malloc`'d only during a transfer and freed immediately
  after parsing — costs zero BSS (was 2 KB static)
- `kRequired` validator table eliminated — replaced with `PNP_STREQ` chain
- All string literals in `.cpp` files use `PNP_STREQ`/`PNP_SNPRINTF` → PROGMEM
- `Interpreter::kMaxVars` = 8 (down from 16)
- ArduinoJson v7 manages parsed document on the heap separately

---

## Config/EEPROM (delivered — schema v4)

`steps_per_mm`, soft travel limits, and other calibration survive a power cycle in
Mega EEPROM. Implemented and host-tested:

```cpp
struct Config {
    static constexpr uint8_t kVersion = 4;
    uint8_t version;
    float   stepsPerMmX, stepsPerMmY1, stepsPerMmY2, stepsPerMmZ;  // per motor
    float   maxTravelMmX, maxTravelMmY, maxTravelMmZ;              // per axis (one Y)
    float   tofOffsetMm[4];                                        // pickup ch0-3
    float   servoDoor*, servoLaserBtn*;                            // angles
    float   probeStep/MaxDepth/Thresh;
    uint16_t crc;            // CRC16/CCITT of all preceding bytes
};
```

- `load()` at boot validates CRC + version; `save()` after `set_cal_distance` /
  `set_max_travel` / `calibrate_sensors`. Invalid/absent → safe defaults.
- `isCalibrated()` (all four motors), `hasTravelLimits()` (all three axes), and
  `isReadyForMotion()` (both) gate headless operation.
- CRC/load/save are schema-agnostic (operate on the whole struct via
  `offsetof`/`sizeof`), so adding fields needs no `Config.cpp` change. EEPROM I/O is
  `#ifdef ARDUINO`; CRC logic is host-tested.
- Dual-Y is stored **per motor** (Y1/Y2) so the two sides can be driven
  independently for anti-racking later with no schema bump.

---

## Build order

| Step | Status | Notes |
|---|---|---|
| 1. Scaffold + comms-first | ✅ Done | GUI connects to Mega over serial |
| 2. Interpreter + validator + chunked transfer | ✅ Done | host-tested |
| 2b. Config/EEPROM (v4) | ✅ Done | per-motor steps/mm, per-axis travel limits, ToF offsets |
| 2c. Soft limits + jog-and-measure calibration | ✅ Done | host-tested; replaces StallGuard traverse |
| 3. Real `Machine` HAL | 🔧 Bench | AccelStepper + TMC2209 UART + `jogAxisSteps` + limit-switch homing + VL53L4CD/mux + servos |
| 4. SafetyMonitor + headless controls | 🔧 Bench | buttons, heartbeat LED, beeper, continuous safety monitors |

---

## Decisions on record

- Arduino IDE build; host tests in a side g++/Unity project.
- `IMachine` seam is mandatory — it is what makes the logic testable.
- No exceptions; no hot-path heap allocation; time injected, not read.
- Libraries: ArduinoJson v7 (now); TMCStepper + AccelStepper (when HAL lands).
- Port faithfully from the Python reference; translate its tests as we go.
- `PNP_STREQ`/`PNP_SNPRINTF` macros in `Platform.h` for PROGMEM string handling.
- `ProgramStore` buffers raw JSON in `malloc`'d heap; passes `const char*` to
  ArduinoJson (forces string copy), then frees immediately — document is
  self-contained and `buf_` can be freed safely.
- Calibration uses operator **jog-and-measure** + supplied distance; no mechanical
  spec knowledge required. Homing is by **limit switches** (StallGuard retired).
  Soft travel limits come from operator-entered per-axis max travel, enforced on MOVE.