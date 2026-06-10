# Firmware Architecture

**Status:** core logic complete, host-tested. Real HAL pending (bench).
MCU firmware for the pick-and-place gantry — Arduino Mega 2560 + RAMPS 1.4.
Built in the Arduino IDE; portable logic unit-tested on the host with g++/Unity.

---

## Guiding idea: the firmware is a port, not a fresh design

The behaviour is already specified and tested in Python — `interpreter.py`,
the simulator's `StateMachine`, `ProgramValidator`, and the protocol — with
189 passing Python tests. The firmware ports that logic to C++. The Python
tests are the spec; they translate directly into the host Unity suite.

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
        Interpreter.h/.cpp      job program executor                 (DONE, tested)
        ProgramValidator.h/.cpp program structure validator          (DONE, tested)
        ProgramStore.h/.cpp     chunked transfer + JSON storage      (DONE, tested)
        Interpreter.h           stub placeholder for future TODOs
      hal/
        IMachine.h              abstract HAL interface
        StubMachine.h           no-hardware impl (comms-first)
        Machine.h               TODO: real hardware (bench)
      protocol/
        Protocol.h/.cpp         JSON-over-serial (Arduino-only layer)
      config/
        Config.h                TODO: EEPROM persistence (next)
      safety/
        SafetyMonitor.h         TODO: laser-park / pickup-loss / StallGuard
      platform/
        Platform.h              PROGMEM macros + host millis() shim
  test/
    MockMachine.h               recording IMachine double
    test_state_machine.cpp      32 tests
    test_interpreter.cpp        69 tests (interpreter + validator)
    unity/                      vendored Unity (ThrowTheSwitch)
    Makefile                    `make` builds and runs all host tests
    ArduinoJson-7.3.1/          vendored ArduinoJson for host builds
  firmware-architecture.md      this file
```

---

## States

Eight states (up from the Python simulator's seven — `CALIBRATING` was added
for the automated steps/mm calibration procedure):

| State | Description |
|---|---|
| `Idle` | Powered on, not homed. Position unknown. |
| `Homing` | Homing sequence running. |
| `Ready` | Homed. Awaiting command. |
| `Running` | Interpreter executing a job program. |
| `Paused` | Interpreter suspended. Resumable. |
| `Faulted` | Hardware or program fault. Operator intervention needed. |
| `Estopped` | E-stop active. All motion stopped. |
| `Calibrating` | Calibration traverse in progress, or awaiting `set_cal_distance`. |

---

## Stepper calibration system

The gantry computes `steps_per_mm` automatically without needing to know belt
pitch, pulley teeth, or microstepping settings. The procedure:

1. `calibrate_axis X` — homes the axis, then drives toward the far hard stop
   using StallGuard until stall is detected. `IMachine::traverseToStop()` returns
   the raw step count.
2. State holds in `Calibrating` — `cal_steps` in the status broadcast goes
   non-zero, signalling the GUI to show the "enter distance" dialog.
3. `set_cal_distance X 420.0` — `steps_per_mm = raw_steps / 420.0`. Stored in
   `StateMachine::stepsPerMm_[]` (runtime) and eventually in `Config`/EEPROM
   (persistence — see Config/EEPROM below).

This approach absorbs belt stretch and mechanical tolerances automatically and
works regardless of the specific hardware configuration.

---

## Unit testing

The host test suite (`Firmware/test/`) compiles the portable `.cpp` files with
g++ against `MockMachine` + Unity — the same framework used at Acclaro. Arduino
IDE builds the same files for the Mega.

```
cd Firmware/test && make    # builds test_state_machine + test_interpreter, runs both
```

**Current totals: 101 tests, 0 failures.**

| Suite | Tests | Covers |
|---|---|---|
| `test_state_machine` | 32 | Command gating, homing, run/pause/estop, transfer, calibration |
| `test_interpreter` | 69 | All interpreter ops, validator, conditions, flow control, WAIT, abort |

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

## Config/EEPROM (next milestone)

`steps_per_mm` and other calibration values must survive a power cycle.

Planned design:
```cpp
struct Config {
    uint8_t  version;           // schema version
    float    stepsPerMm[3];     // X, Y, Z
    // future: servo angles, probe params, named positions, ...
    uint16_t crc;               // CRC16 of all preceding bytes
};
```

- `Config::load()` at boot: validates CRC + version; populates `StateMachine`
- `Config::save()` after `set_cal_distance`: persists updated value
- Invalid/absent EEPROM → safe defaults, `config_invalid` fault logged
- The EEPROM I/O is Arduino-specific (`#ifdef ARDUINO`); CRC logic is portable
  and host-testable

---

## Build order

| Step | Status | Notes |
|---|---|---|
| 1. Scaffold + comms-first | ✅ Done | GUI connects to Mega over serial |
| 2. Interpreter + validator + chunked transfer | ✅ Done | 101 host tests passing |
| 2b. Config/EEPROM | ⏳ Next | Last host-testable piece |
| 3. Real `Machine` HAL | 🔧 Bench | AccelStepper + TMC2209 UART + StallGuard + ToF + servos |
| 4. SafetyMonitor + headless controls | 🔧 Bench | Buttons, LEDs, beeper, continuous safety monitors |

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
- Calibration uses automated StallGuard traverse + user-supplied distance;
  no mechanical spec knowledge required.
