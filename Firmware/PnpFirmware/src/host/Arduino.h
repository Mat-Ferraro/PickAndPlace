#pragma once
// ---------------------------------------------------------------------------
// Minimal host shim for <Arduino.h>.
//
// Lets the Arduino-only serial layer (Protocol.cpp) and ArduinoJson's Print
// writer compile and run on the desktop, so the *real* firmware protocol code
// can be exercised against the GUI/simulator wire format without a Mega.
//
// Provides just enough: Print (write) + Stream (available/read/peek).
// millis() is intentionally NOT defined here — Platform.h owns it on host.
// ---------------------------------------------------------------------------
#include <cstdint>
#include <cstddef>

class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t c) = 0;
  virtual size_t write(const uint8_t* buf, size_t n) {
    size_t w = 0;
    for (size_t i = 0; i < n; ++i) w += write(buf[i]);
    return w;
  }
  size_t print(const char* s)   { size_t n = 0; while (*s) { write((uint8_t)*s++); ++n; } return n; }
  size_t println()              { return write((uint8_t)'\n'); }
  size_t println(const char* s) { size_t n = print(s); return n + println(); }
};

class Stream : public Print {
 public:
  virtual int available() = 0;
  virtual int read()      = 0;
  virtual int peek()      = 0;
};
