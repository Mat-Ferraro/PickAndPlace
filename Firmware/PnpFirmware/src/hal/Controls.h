#pragma once
#include <Arduino.h>
#include "../core/StateMachine.h"

namespace pnp {

// ===========================================================================
// Controls — physical Start / Pause buttons + E-stop, polled and debounced,
// synthesized into StateMachine events. Lives in a header (not the .ino) so the
// Arduino IDE's auto-prototype pass doesn't choke on the Button struct.
//
// Start (D3) and Pause (D14): momentary, active-low (INPUT_PULLUP, pressed=LOW).
// E-stop (D18): normally-closed on INPUT_PULLUP -> closed=LOW (normal),
// open=HIGH (pressed OR broken wire), i.e. fail-safe active-high.
// >>> VERIFY these pins and the E-stop NC polarity match your wiring. <<<
// ===========================================================================
class Controls {
 public:
  static const uint8_t kStartPin = 3;
  static const uint8_t kPausePin = 14;
  static const uint8_t kEstopPin = 18;

  explicit Controls(StateMachine& sm) : sm_(sm) {}

  void begin() {
    pinMode(kStartPin, INPUT_PULLUP);
    pinMode(kPausePin, INPUT_PULLUP);
    pinMode(kEstopPin, INPUT_PULLUP);
  }

  void poll(uint32_t now) {
    if (edge(start_, now)) sm_.pressButton("start", now);
    if (edge(pause_, now)) sm_.pressButton("pause", now);
    sm_.setButtonLevels(start_.stable, pause_.stable);   // live levels for the GUI
    bool es = (digitalRead(kEstopPin) == HIGH);   // NC: HIGH = triggered
    if (es != estop_) { estop_ = es; sm_.setEstopHardware(es); }
  }

 private:
  struct Btn { uint8_t pin; bool stable; bool raw; uint32_t changed; };
  static const uint32_t kDebounceMs = 25;

  // Debounced rising-edge (press) detector for an active-low button.
  bool edge(Btn& b, uint32_t now) {
    bool raw = (digitalRead(b.pin) == LOW);
    if (raw != b.raw) { b.raw = raw; b.changed = now; }
    if (now - b.changed >= kDebounceMs && raw != b.stable) {
      b.stable = raw;
      return raw;                     // true only on the press transition
    }
    return false;
  }

  StateMachine& sm_;
  Btn  start_{kStartPin, false, false, 0};
  Btn  pause_{kPausePin, false, false, 0};
  bool estop_ = false;
};

}  // namespace pnp
