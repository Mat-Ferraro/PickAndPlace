# Pick-and-Place Paper Loader — Project Documentation

This folder is the reorganized project documentation package. Version v0.7 incorporates the review notes from the v0.6 package audit.
The single large document has been split into focused files so that the stable
design, the firmware/GUI contract, the parts list, and the live open questions
can each evolve independently.

## Document set

| File | Purpose |
|---|---|
| `architecture.md` | Stable system design: hardware overview, motion, power, sensors, states, safety. |
| `pin-mapping.md` | RAMPS 1.4 + Arduino Mega pin reference, verified against the Mega pinout. |
| `communication-protocol.md` | The JSON-over-USB command/status contract between GUI and firmware. |
| `components-and-references.md` | Module inventory with datasheet-verified specs, plus the reference-doc index (what we have / still need). |
| `open-decisions.md` | Live decision log — the questions that are not yet settled. |
| `CHANGELOG.md` | Version history. |

## Conventions

- **"Decided" vs "open."** Anything still under debate lives in `open-decisions.md`,
  not in `architecture.md`. Keep settled design in the architecture doc and move
  resolved items out of the decision log when they are locked.
- **Datasheet facts are cited inline** as `[ref: <doc>]` pointing at an entry in
  `components-and-references.md`, so a spec can always be traced to its source PDF.
- **Pin numbers** follow the Arduino `Dnn` digital numbering; RAMPS header names
  are given alongside.

## Current status (snapshot)

Bench bring-up verified so far: Arduino IDE upload to the Mega over USB-B,
onboard LED blink, USB serial via Arduino Serial Monitor / host serial tool,
external pushbutton via `INPUT_PULLUP`, 3-wire E-stop/stop button as a
dry-contact input, MG90S-style servo movement, RAMPS 1.4 physically fitted, and
TMC2209 drivers physically installed. Stepper motion through RAMPS + TMC2209 is
not yet validated.

Not yet validated: stepper motion through RAMPS + TMC2209, driver current limits
under load, the vacuum pump driver, solenoid actuation in this integrated build,
servo-vs-solenoid vacuum release, VL53L0X wiring through the TCA9548A, full
headless job execution, and hardware-enforced E-stop power removal.

**Open architecture question (blocks parts of motion + GUI):** is this a Cartesian
gantry (X / dual-Y / Z, as the pin tables assume) or an articulated/SCARA arm?
The two imply very different motion math and jog/teach interfaces. Tracked in
`open-decisions.md`.
