#include "Protocol.h"
#include <ArduinoJson.h>   // install via Library Manager (ArduinoJson v7)

namespace pnp {

void Protocol::begin(Stream& io) { io_ = &io; }

void Protocol::poll(uint32_t nowMs) {
  if (!io_) return;
  while (io_->available()) {
    char c = (char)io_->read();
    if (c == '\n' || c == '\r') {
      if (len_ > 0) {
        buf_[len_] = '\0';
        handleLine(buf_, nowMs);
        len_ = 0;
      }
    } else if (len_ < sizeof(buf_) - 1) {
      buf_[len_++] = c;
    } else {
      len_ = 0;  // TODO: oversized line -> nack "oversized" + chunked transfer
    }
  }
}

void Protocol::handleLine(const char* line, uint32_t nowMs) {
  JsonDocument doc;
  if (deserializeJson(doc, line)) {
    JsonDocument out;
    out["type"] = "nack";
    out["reason"] = "malformed";
    serializeJson(out, *io_);
    io_->println();
    return;
  }

  Command cmd;
  cmd.name     = doc["cmd"] | "";
  cmd.id       = doc["id"]  | -1;
  cmd.paramKey = doc["key"] | "";
  cmd.calAxis  = parseCalAxis(doc["axis"] | "X");
  cmd.mm       = doc["mm"]  | 0.0f;   // set_cal_distance / set_max_travel magnitude
  cmd.steps    = doc["steps"] | 0;    // cal_jog raw step count

  Response r = sm_.handleCommand(cmd, nowMs);
  sendResponse(r);
  sendStatus();   // push fresh state immediately after a command
}

void Protocol::sendResponse(const Response& r) {
  if (!io_ || r.kind == Response::None) return;
  JsonDocument out;
  out["type"] = (r.kind == Response::Ack) ? "ack" : "nack";
  if (r.id >= 0) out["id"] = r.id;
  out["cmd"] = r.cmd;
  if (r.kind == Response::Nack) out["reason"] = r.reason;
  if (r.hasParamValue) {
      out["key"]   = r.paramKey;   // the actual param key (e.g. steps_per_mm_x)
      out["value"] = r.paramValue;
  }
  if (r.hasTofOffsets) {
      JsonArray arr = out["offsets"].to<JsonArray>();
      for (int i = 0; i < 4; i++) arr.add(r.tofOffsets[i]);
  }
  serializeJson(out, *io_);
  io_->println();
}

void Protocol::sendStatus() {
  if (!io_) return;
  StatusSnapshot s = sm_.buildStatus();
  JsonDocument out;
  out["type"]             = "status";
  out["state"]            = stateName(s.state);
  out["program_loaded"]   = s.programLoaded;
  out["pickup_ok"]        = s.pickupOk;
  out["material_present"] = s.materialPresent;
  out["laser_safe"]       = s.laserSafe;
  out["estop_hw"]         = s.estopHw;
  out["fault"]            = s.fault;   // nullptr -> JSON null automatically
  out["cal_axis"]         = s.calAxis; // nullptr -> null unless calibrating
  out["cal_steps"]        = s.calSteps;
  serializeJson(out, *io_);
  io_->println();
}

void Protocol::maybeBroadcastStatus(uint32_t nowMs) {
  if ((uint32_t)(nowMs - lastStatusMs_) >= kStatusPeriodMs) {
    lastStatusMs_ = nowMs;
    sendStatus();
  }
}

}  // namespace pnp