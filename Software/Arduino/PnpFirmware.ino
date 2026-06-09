/*
 * PnpFirmware.ino — Pick-and-Place gantry firmware (Arduino Mega 2560 + RAMPS).
 *
 * Scaffold / comms-first milestone: this wires the serial Protocol to the
 * portable StateMachine driving a no-hardware StubMachine. Flash it, connect
 * the GUI over serial at 115200, and you can drive state transitions and watch
 * status broadcasts with no motors attached — the GUI can't tell it apart from
 * the Python simulator, because it's the same protocol.
 *
 * Build: Arduino IDE, board "Arduino Mega 2560". Install "ArduinoJson" (v7)
 * via Library Manager. (Later: TMCStepper, AccelStepper for the real HAL.)
 *
 * Logic lives in src/ and is unit-tested on the host — see Firmware/test/.
 */

#include "src/core/StateMachine.h"
#include "src/hal/StubMachine.h"
#include "src/protocol/Protocol.h"

using namespace pnp;

static StubMachine  machine;                 // TODO: swap for the real Machine
static StateMachine stateMachine(machine);
static Protocol     protocol(stateMachine);

void setup() {
  Serial.begin(115200);
  protocol.begin(Serial);
  // TODO: Config::load() to restore positions/params and set programLoaded.
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
