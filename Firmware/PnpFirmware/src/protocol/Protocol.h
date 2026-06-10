#pragma once
#include <Arduino.h>
#include "../core/StateMachine.h"

// Serial protocol layer (Arduino-only — the single place that touches Serial
// and ArduinoJson). Reads newline-delimited JSON commands, dispatches them to
// the portable StateMachine, and emits ack / nack / status. Because this speaks
// the exact wire format in Documentation/communication-protocol.md, the
// existing GUI connects to the Mega over serial with no changes — same protocol
// it already uses against the Python simulator over TCP.

namespace pnp {

class Protocol {
 public:
  explicit Protocol(StateMachine& sm) : sm_(sm) {}

  void begin(Stream& io);
  void poll(uint32_t nowMs);                  // read + dispatch pending lines
  void maybeBroadcastStatus(uint32_t nowMs);  // periodic status push

 private:
  void handleLine(const char* line, uint32_t nowMs);
  void sendResponse(const Response& r);
  void sendStatus();

  StateMachine& sm_;
  Stream*       io_ = nullptr;
  char          buf_[256];
  uint16_t      len_ = 0;
  uint32_t      lastStatusMs_ = 0;

  static constexpr uint32_t kStatusPeriodMs = 250;
};

}  // namespace pnp
