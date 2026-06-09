# Firmware Architecture

Status: scaffold (comms-first milestone). MCU firmware for the pick-and-place
gantry — Arduino Mega 2560 + RAMPS 1.4. Built in the Arduino IDE, with the
portable logic unit-tested on the host. This document records the structure and
the decisions behind it; it complements `architecture.md` (system) and
`communication-protocol.md` (wire format).

## Guiding idea: the firmware is a *port*, not a fresh design

The behaviour is already specified and tested in Python — `interpreter.py`, the
simulator's `StateMachine`, `ProgramValidator`, and the protocol, with 189
passing tests. The firmware ports that logic to C++. The Python tests are the
spec the C++ must satisfy, and they translate into the host test suite.

## The one structural rule: logic depends only on `IMachine`

Every layer above the hardware (state machine, interpreter, protocol) depends
only on the abstract `IMachine` interface — never on Arduino APIs directly.
`IMachine` is the C++ twin of the Python `MachineInterface`/`FakeMachine` seam.

- On the Mega, `IMachine` is implemented by `Machine` (real peripherals).
- In tests, it's implemented by `MockMachine` (records calls, scriptable) — the
  twin of `FakeMachine` in `conftest.py`.
- For comms-first bring-up, `StubMachine` (no hardware) lets the upper layers
  run on the Mega before any driver exists.

Adaptations from Python for AVR: no exceptions (abortable operations return
`OpResult` instead of raising `ProgramFault`); no dynamic allocation in the hot
path; time is **injected** (`tick(nowMs)`) rather than read from a clock, which
keeps transitions deterministic under host tests.

## Layout

```
Firmware/
  PnpFirmware/                  Arduino sketch (open this in the IDE)
    PnpFirmware.ino             setup()/loop(): wires protocol + SM + stub HAL
    src/                        compiled recursively by the Arduino IDE
      core/
        State.h                 7-state enum + bitmask helper
        StateMachine.h/.cpp     state machine + command gating  (PORTED, tested)
        Interpreter.h           TODO: port of interpreter.py
        ProgramValidator.h      TODO: port of ProgramValidator
      hal/
        IMachine.h              abstract HAL interface  ← the FakeMachine seam
        StubMachine.h           no-hardware impl (comms-first)
        Machine.h               TODO: real hardware impl (bench-validated)
      protocol/
        Protocol.h/.cpp         JSON-over-serial (only ArduinoJson/Serial user)
      config/Config.h           TODO: EEPROM config + program storage + CRC
      safety/SafetyMonitor.h    TODO: laser-park / pickup-loss / StallGuard
      platform/Platform.h       host millis() shim for off-target builds
  test/                         host (g++) Unity suite — runs on the PC
    MockMachine.h               recording IMachine double (= FakeMachine)
    test_state_machine.cpp      first ported tests (12, passing)
    unity/                      vendored Unity (ThrowTheSwitch)
    Makefile                    `make` builds + runs the host tests
```

Includes inside `src/` are file-relative (e.g. `"../hal/IMachine.h"`) so the
Arduino IDE resolves them without special include-path configuration.

## Unit testing (Arduino IDE has no host runner — we bolt one on)

The portable layers (state machine, and later interpreter / validator / config)
compile under host `g++` against `MockMachine` + Unity, the same framework used
in the Acclaro host harnesses. Arduino IDE builds the *same* `.cpp` files for
the Mega; the host suite in `Firmware/test/` builds them for the PC.

```
cd Firmware/test && make        # builds and runs the host tests
```

What is **not** host-tested: the `Machine` HAL implementation — "does the
TMC2209 actually step" is validated on the bench, not in a unit test.

## Comms-first, and why the GUI "just works"

`Protocol` speaks the exact wire format in `communication-protocol.md`, which is
the same protocol the GUI already uses against the Python simulator over TCP. So
once the protocol + state machine run on the Mega (even with `StubMachine`), the
GUI connects over serial with **no GUI changes** — point it at the COM port
instead of the simulator socket. The current scaffold already answers
`query_status`, gates commands, applies the core transitions
(home/run/pause/resume/reset/estop), and broadcasts status.

## Build order

1. **Scaffold + comms-first** (this milestone): `IMachine`/`MockMachine`/
   `StubMachine`, `StateMachine`, `Protocol`, host tests. GUI can connect.
2. **Interpreter + validator + Config/EEPROM + chunked transfer** — ports of the
   Python, host-tested against the translated suite.
3. **Real `Machine` HAL** — steppers/TMC2209 UART/StallGuard/ToF/servos/outputs,
   validated on the bench (the current hardware bring-up work plugs in here).
4. **SafetyMonitor + headless controls** — buttons/LEDs/beeper, continuous
   monitors raising faults into the state machine.

## Decisions on record

- Build in the Arduino IDE; host tests live in a side `g++`/Unity project.
- `IMachine` abstraction is mandatory — it is what makes the logic testable.
- No exceptions / no hot-path allocation; time injected, not read.
- Libraries: ArduinoJson (v7) now; TMCStepper + AccelStepper when the HAL lands.
- Port faithfully from the Python reference; translate its tests as we go.
