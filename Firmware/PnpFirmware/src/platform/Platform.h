#pragma once
// Platform shim — PROGMEM helpers + host millis() stub.
//
// On AVR, string literals referenced as const char* are copied from flash
// into SRAM at startup and counted against the 8 KB SRAM budget. Two macros
// fix this by keeping strings in flash:
//
//   PNP_STREQ(ramStr, "literal")
//       strcmp on host; strcmp_P (reads literal from flash) on AVR.
//
//   PNP_SNPRINTF(buf, len, "fmt", ...)
//       snprintf on host; snprintf_P (format stays in flash) on AVR.
//
// Use these in every .cpp file that has string literals used only for
// comparison or formatting — the compiler moves them to .text (flash) instead
// of .data/.rodata (SRAM).

#ifdef ARDUINO
  #include <Arduino.h>
  #include <avr/pgmspace.h>
  #define PNP_STREQ(ram, literal)          (strcmp_P((ram), PSTR(literal)) == 0)
  #define PNP_SNPRINTF(buf, len, fmt, ...) snprintf_P((buf), (len), PSTR(fmt), ##__VA_ARGS__)
#else
  #include <stdint.h>
  #include <stdio.h>
  #include <string.h>
  #include <chrono>
  #define PNP_STREQ(ram, literal)          (strcmp((ram), (literal)) == 0)
  #define PNP_SNPRINTF(buf, len, fmt, ...) snprintf((buf), (len), (fmt), ##__VA_ARGS__)
  inline uint32_t millis() {
    using namespace std::chrono;
    static const auto t0 = steady_clock::now();
    return (uint32_t)duration_cast<milliseconds>(steady_clock::now() - t0).count();
  }
#endif
