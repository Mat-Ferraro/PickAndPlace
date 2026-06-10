/*
 * PnpFirmware.ino — Pick-and-Place gantry firmware (Arduino Mega 2560 + RAMPS).
 *
 * Current milestone: full portable logic (state machine, interpreter,
 * validator, chunked transfer, calibration) wired to a no-hardware StubMachine.
 * Flash and connect the GUI over serial at 115200 to drive state transitions,
 * load and run programs, and calibrate axes — all without motors attached.
 *
 * Build: Arduino IDE, board "Arduino Mega 2560".
 * Libraries (install via Library Manager): ArduinoJson v7.
 * Later (when real HAL lands): TMCStepper, AccelStepper.
 *
 * Logic lives in src/ and is unit-tested on the host — see Firmware/test/.
 */

#include "src/core/StateMachine.h"
#include "src/config/Config.h"
#include "src/hal/StubMachine.h"
#include "src/protocol/Protocol.h"

using namespace pnp;

static Config       config;
static StubMachine  machine;                 // TODO: swap for the real Machine
static StateMachine stateMachine(machine, config);
static Protocol     protocol(stateMachine);

void setup() {
  Serial.begin(115200);
  config.load();          // populate from EEPROM; safe defaults on first boot
  protocol.begin(Serial);
  // TODO: Controls::begin() for buttons / status LED / beeper.
}

void loop() {
  const uint32_t now = millis();
  protocol.poll(now);                 // read + dispatch serial commands
  stateMachine.tick(now);             // time-driven transitions (homing, ...)
  protocol.maybeBroadcastStatus(now); // periodic status to the GUI
  // TODO: SafetyMonitor::poll(now)  — laser-park / pickup-loss / StallGuard.
  // TODO: Controls::poll(now)       — synthesize button presses into the SM.
}
