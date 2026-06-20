/*
 * PnpFirmware.ino — Pick-and-Place gantry firmware (Arduino Mega 2560 + RAMPS).
 *
 * HARDWARE BRING-UP, SLICE 1: real Machine HAL for OUTPUTS (pump, solenoid,
 * servos, beeper) + physical button/E-stop reading (Controls). Motion (steppers)
 * is still a safe no-op stub inside Machine — no motor moves yet. Drive outputs
 * from the GUI Service tab; press the physical Start/Pause/E-stop and watch state.
 *
 * Build: Arduino IDE, board "Arduino Mega 2560".
 * Libraries (Library Manager): ArduinoJson v7, Servo (bundled).
 *
 * Logic in src/ is host-unit-tested (Firmware/test/). Machine.h / Controls.h /
 * this sketch are validated on the bench.
 */

#include "src/core/StateMachine.h"
#include "src/config/Config.h"
#include "src/hal/Machine.h"
#include "src/hal/Controls.h"
#include "src/protocol/Protocol.h"

using namespace pnp;

static Config       config;
static Machine      machine;                 // real HAL (outputs real, motion stubbed)
static StateMachine stateMachine(machine, config);
static Protocol     protocol(stateMachine);
static Controls     controls(stateMachine);  // physical buttons + E-stop

// ---- Heartbeat LED -------------------------------------------------------
// ~1 Hz blink proving the loop is alive. D13 = RAMPS LED1 (the visible status
// LED; the onboard Mega LEDs are hidden under the shield). Move to D8 only if
// you wire an SD card (D13 is SPI SCK).
static const uint8_t  kHeartbeatPin        = LED_BUILTIN;  // D13 / LED1
static const uint32_t kHeartbeatHalfPeriod = 500;          // 1 Hz

static void heartbeat(uint32_t now) {
  static uint32_t last = 0;
  static bool     on   = false;
  if (now - last >= kHeartbeatHalfPeriod) { last = now; on = !on;
    digitalWrite(kHeartbeatPin, on ? HIGH : LOW); }
}

void setup() {
  Serial.begin(115200);
  pinMode(kHeartbeatPin, OUTPUT);
  digitalWrite(kHeartbeatPin, LOW);
  machine.begin();          // pin modes, servos attached, outputs off
  controls.begin();         // button / E-stop inputs
  config.load();            // populate from EEPROM; safe defaults on first boot
  protocol.begin(Serial);
}

void loop() {
  const uint32_t now = millis();
  protocol.poll(now);                 // read + dispatch serial commands
  stateMachine.tick(now);             // time-driven transitions (homing, ...)
  protocol.maybeBroadcastStatus(now); // periodic status to the GUI
  controls.poll(now);                 // physical Start / Pause / E-stop
  heartbeat(now);                     // ~1 Hz "firmware alive" blink
  // TODO Slice 2: SafetyMonitor::poll(now) — laser-park / pickup-loss (ToF).
}
