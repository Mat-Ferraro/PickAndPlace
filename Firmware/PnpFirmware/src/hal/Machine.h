#pragma once
#include <Arduino.h>
#include <Servo.h>
#include <Wire.h>
#include <VL53L4CD.h>   // Pololu vl53l4cd-arduino library
#include "IMachine.h"

namespace pnp {

// ===========================================================================
// Machine — real hardware HAL (Arduino Mega 2560 + RAMPS 1.4).
//
// IMPLEMENTED:
//   - Outputs: pump, valve, the two 2-position servos, beeper.
//   - jogAxisSteps(): REAL raw STEP/DIR jog, one motor, E-stop checked every
//     step. Needs NO calibration — this is the primitive you USE to calibrate
//     (jog a known step count, measure travel -> steps/mm).
//
// STILL SAFE NO-OP STUBS (next slice):
//   - moveTo() / home() / probeZ()  (mm moves need steps/mm + a homed origin)
//   - readDistanceMm()              (ToF)
//
// >>> VERIFY EVERY PIN BELOW MATCHES YOUR ACTUAL WIRING BEFORE FLASHING. <<<
// Bench-bring-up ritual for the FIRST jog of each axis:
//   1. Set driver Vref LOW (low current) first.
//   2. Jog ONE axis a small amount (e.g. 50-100 steps).
//   3. Confirm it moves and note the direction. If reversed, flip kDir* below
//      (or swap the coil pair) — do NOT "fix" direction in the GUI.
//   4. E-stop is polled every step; pressing it aborts the jog immediately.
// ===========================================================================
class Machine : public IMachine {
 public:
  // ---- Output pin map (EDIT TO MATCH YOUR WIRING) ------------------------
  static const uint8_t kPumpIn1 = 23;   // L298N IN1
  static const uint8_t kPumpIn2 = 25;   // L298N IN2
  static const uint8_t kPumpEna = 11;   // L298N ENA (PWM)
  static const uint8_t kValve = 6;      // AOD4184 gate (PWM-capable)
  static const uint8_t kServoDoorPin     = 5;
  static const uint8_t kServoLaserBtnPin = 4;
  static const uint8_t kBeeper = 39;

  // ---- Stepper pin map (RAMPS 1.4; ENABLE is ACTIVE-LOW) -----------------
  // Socket map (bench-confirmed): X->X, Y1->Y, Z->E0, Y2->E1.  STEP/DIR mode.
  static const uint8_t kXStep = 54,  kXDir = 55,  kXEn = 38;   // X socket
  static const uint8_t kY1Step = 60, kY1Dir = 61, kY1En = 56;  // Y  socket
  static const uint8_t kZStep = 26,  kZDir = 28,  kZEn = 24;   // E0 socket
  static const uint8_t kY2Step = 36, kY2Dir = 34, kY2En = 30;  // E1 socket

  // ---- Direction sense (flip a value if an axis jogs the wrong way) ------
  // The DIR level written for a POSITIVE jog. If +jog goes the wrong way,
  // change HIGH<->LOW here (per axis) rather than negating in the GUI.
  static const uint8_t kXDirPos  = HIGH;
  static const uint8_t kY1DirPos = HIGH;
  static const uint8_t kY2DirPos = HIGH;
  static const uint8_t kZDirPos  = HIGH;

  // ---- Step timing -------------------------------------------------------
  // Conservative, constant-rate jog (no acceleration). ~1.25 kHz step rate is
  // well under typical start/stop speed, so no missed steps from standstill.
  static const uint16_t kStepHighUs = 4;     // STEP pulse high (>= driver min)
  static const uint16_t kStepLowUs  = 400;   // gap between steps (sets speed)

  // ---- E-stop input (owned/configured by Controls; we only read it) ------
  // NC switch to GND with INPUT_PULLUP: closed/normal = LOW, pressed = HIGH.
  static const uint8_t kEstopPin = 18;

  // ---- Servo angles (BENCH-TUNE THESE) -----------------------------------
  static const int kDoorOpenDeg     = 90;
  static const int kDoorClosedDeg   = 0;
  static const int kLaserPressDeg   = 60;
  static const int kLaserReleaseDeg = 0;

  // Call once from setup() before use.
  void begin() {
    pinMode(kPumpIn1, OUTPUT);
    pinMode(kPumpIn2, OUTPUT);
    pinMode(kPumpEna, OUTPUT);
    pinMode(kValve, OUTPUT);
    pinMode(kBeeper, OUTPUT);

    // Safe initial state: everything off.
    digitalWrite(kPumpIn1, LOW);
    digitalWrite(kPumpIn2, LOW);
    analogWrite(kPumpEna, 0);
    digitalWrite(kValve, LOW);
    digitalWrite(kBeeper, LOW);

    // Steppers: pins as outputs, drivers DISABLED at boot (EN HIGH = off),
    // so nothing holds torque or heats up until a jog enables it.
    initStepperPins(kXStep,  kXDir,  kXEn);
    initStepperPins(kY1Step, kY1Dir, kY1En);
    initStepperPins(kY2Step, kY2Dir, kY2En);
    initStepperPins(kZStep,  kZDir,  kZEn);

    doorServo_.attach(kServoDoorPin);
    laserServo_.attach(kServoLaserBtnPin);
    doorServo_.write(kDoorClosedDeg);
    laserServo_.write(kLaserReleaseDeg);

    // ToF: VL53L4CD sensors behind a TCA9548A mux (all at 0x29; the mux routes
    // one channel at a time). Sensors are init'd lazily on first read of each
    // channel, so this works with 1 sensor (only its channel comes up) or all 6
    // without knowing the wiring here.
    Wire.begin();
    tof_.setBus(&Wire);
    tof_.setTimeout(100);
    if (kUseMux) {
      pinMode(kMuxRstPin, OUTPUT);
      resetMux();                 // pulse RST low->high for a clean mux state
    } else {
      // Single sensor wired straight to the bus (no mux): init it now on ch0.
      if (tof_.init()) {
        tof_.setRangeTiming(50, 0);   // 50 ms budget, continuous
        tof_.startContinuous();
      }
    }
  }

  // ---- Outputs (REAL) ----------------------------------------------------
  void setOutput(const char* name, bool value) override {
    if (strcmp(name, "pump") == 0) {
      if (value) {
        digitalWrite(kPumpIn1, HIGH);
        digitalWrite(kPumpIn2, LOW);
        analogWrite(kPumpEna, 255);
      } else {
        digitalWrite(kPumpIn1, LOW);
        digitalWrite(kPumpIn2, LOW);
        analogWrite(kPumpEna, 0);
      }
    } else if (strcmp(name, "valve") == 0) {
      digitalWrite(kValve, value ? HIGH : LOW);
    } else if (strcmp(name, "servo_door") == 0) {
      doorServo_.write(value ? kDoorOpenDeg : kDoorClosedDeg);
    } else if (strcmp(name, "servo_laser_btn") == 0) {
      laserServo_.write(value ? kLaserPressDeg : kLaserReleaseDeg);
    } else if (strcmp(name, "beeper") == 0) {
      digitalWrite(kBeeper, value ? HIGH : LOW);
    }
    // Unknown names are ignored (no-op).
  }

  bool readSensor(const char* /*name*/) override {
    return false;                    // ToF-derived sensors not wired yet
  }

  OpResult delayMs(uint32_t ms) override {
    delay(ms);
    return OpResult::Ok;
  }

  void log(const char* msg) override {
    Serial.print(F("{\"type\":\"log\",\"msg\":\""));
    Serial.print(msg);
    Serial.println(F("\"}"));
  }

  Position getPosition() override { return pos_; }

  // ---- Motion ------------------------------------------------------------
  // REAL raw jog. "Y" drives BOTH gantry motors in lockstep (one side alone
  // stalls against the other). "X"/"Y1"/"Y2"/"Z" drive a single motor — Y1/Y2
  // individually are for homing/squaring. Blocking, E-stop polled every step.
  // Does NOT touch mm bookkeeping (pos_) — raw steps are pre-calibration.
  OpResult jogAxisSteps(const char* axis, int32_t steps) override {
    if (steps == 0) return OpResult::Ok;
    if (estopTriggered()) return OpResult::Aborted;

    if (strcmp(axis, "Y") == 0) {
      AxisPins a = {kY1Step, kY1Dir, kY1En, kY1DirPos, true};
      AxisPins b = {kY2Step, kY2Dir, kY2En, kY2DirPos, true};
      return jogPair(a, b, steps);          // both Y motors together
    }
    AxisPins ap = pinsForAxis(axis);         // X, Y1, Y2, Z (single motor)
    if (!ap.valid) { log("jog: unknown axis"); return OpResult::Faulted; }
    AxisPins none = {0, 0, 0, 0, false};
    return jogPair(ap, none, steps);
  }

  // ---- Still stubbed (next slice: mm moves + limit-switch homing) --------
  OpResult moveTo(float x, float y, float z, uint8_t = 80) override {
    pos_ = {x, y, z};                // bookkeeping only — no motion yet
    return OpResult::Ok;
  }
  OpResult probeZ(float x, float y, float, float, float, float, float& outZ) override {
    outZ = 0.0f; pos_ = {x, y, 0.0f};
    return OpResult::Ok;
  }
  OpResult home(const char*) override {
    pos_ = {0, 0, 0};                // no real homing yet
    return OpResult::Ok;
  }

  // ---- ToF -------------------------------------------------------------
  // Returns mm in outMm, or -1.0 if invalid / no sensor on that channel.
  // With the mux, selects the channel then reads; each channel's sensor is
  // init'd on first use (so unused channels just report invalid).
  OpResult readDistanceMm(uint8_t channel, float& outMm) override {
    outMm = -1.0f;
    if (channel >= kTofChannels) return OpResult::Ok;
    if (!kUseMux && channel != 0) return OpResult::Ok;  // single direct sensor = ch0

    if (kUseMux) selectMuxChannel(channel);

    if (!tofInited_[channel]) {
      if (tof_.init()) {                 // a sensor answered on this channel
        tof_.setTimeout(100);
        tof_.setRangeTiming(50, 0);      // 50 ms budget, continuous
        tof_.startContinuous();
        tofInited_[channel] = true;
      } else {
        return OpResult::Ok;             // nothing here -> invalid
      }
    }
    uint16_t mm = tof_.read(false);      // non-blocking: latest reading
    if (!tof_.timeoutOccurred() && tof_.ranging_data.range_status == 0) {
      outMm = (float)mm;
    }
    return OpResult::Ok;
  }

 private:
  struct AxisPins { uint8_t step, dir, en, dirPos; bool valid; };

  AxisPins pinsForAxis(const char* axis) {
    if (strcmp(axis, "X")  == 0) return {kXStep,  kXDir,  kXEn,  kXDirPos,  true};
    if (strcmp(axis, "Y1") == 0) return {kY1Step, kY1Dir, kY1En, kY1DirPos, true};
    if (strcmp(axis, "Y2") == 0) return {kY2Step, kY2Dir, kY2En, kY2DirPos, true};
    if (strcmp(axis, "Z")  == 0) return {kZStep,  kZDir,  kZEn,  kZDirPos,  true};
    return {0, 0, 0, 0, false};
  }

  static uint8_t dirLevel(uint8_t dirPos, bool positive) {
    return positive ? dirPos : (dirPos == HIGH ? LOW : HIGH);
  }

  // Pulse one motor (b.valid == false) or two motors in lockstep. Per-step
  // E-stop check. Drivers left enabled (holding torque) on completion.
  OpResult jogPair(const AxisPins& a, const AxisPins& b, int32_t steps) {
    const bool positive = (steps > 0);
    const uint32_t n = positive ? (uint32_t)steps : (uint32_t)(-steps);

    digitalWrite(a.en, LOW);
    digitalWrite(a.dir, dirLevel(a.dirPos, positive));
    if (b.valid) {
      digitalWrite(b.en, LOW);
      digitalWrite(b.dir, dirLevel(b.dirPos, positive));
    }
    delayMicroseconds(10);                            // DIR setup time

    for (uint32_t i = 0; i < n; ++i) {
      if (estopTriggered()) return OpResult::Aborted; // per-step safety
      digitalWrite(a.step, HIGH);
      if (b.valid) digitalWrite(b.step, HIGH);
      delayMicroseconds(kStepHighUs);
      digitalWrite(a.step, LOW);
      if (b.valid) digitalWrite(b.step, LOW);
      delayMicroseconds(kStepLowUs);
    }
    return OpResult::Ok;
  }

  static void initStepperPins(uint8_t s, uint8_t d, uint8_t e) {
    pinMode(s, OUTPUT); pinMode(d, OUTPUT); pinMode(e, OUTPUT);
    digitalWrite(s, LOW);
    digitalWrite(d, LOW);
    digitalWrite(e, HIGH);           // active-low EN: HIGH = disabled
  }

  static bool estopTriggered() {
    return digitalRead(kEstopPin) == HIGH;   // NC+pullup: HIGH = pressed/open
  }

  // ToF / mux. kUseMux is now TRUE: every readDistanceMm(ch) routes the mux to
  // channel ch before reading. RST (active-low) is on a Mega GPIO so firmware
  // can hard-reset the mux for bus recovery.
  static const bool    kUseMux      = true;
  static const uint8_t kMuxAddr     = 0x70;   // TCA9548A (A0/A1/A2 -> GND)
  static const uint8_t kMuxRstPin   = 17;     // D17 -> mux RST (active-low)
  static const uint8_t kTofChannels = 6;      // ch0-3 pickup, ch4 home, ch5 material
  static void selectMuxChannel(uint8_t ch) {
    Wire.beginTransmission(kMuxAddr);
    Wire.write((uint8_t)(1u << ch));
    Wire.endTransmission();
  }
  void resetMux() {
    digitalWrite(kMuxRstPin, LOW);
    delayMicroseconds(10);
    digitalWrite(kMuxRstPin, HIGH);
    delay(2);
    for (uint8_t c = 0; c < kTofChannels; c++) tofInited_[c] = false;
  }

  Servo    doorServo_;
  Servo    laserServo_;
  VL53L4CD tof_;
  bool     tofInited_[6] = {false, false, false, false, false, false};
  Position pos_{0, 0, 0};
};

}  // namespace pnp
