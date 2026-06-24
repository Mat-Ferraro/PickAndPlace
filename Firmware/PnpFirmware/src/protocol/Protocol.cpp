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
  cmd.output   = doc["output"]   | "";    // set_output name
  cmd.state    = doc["state"]    | false; // set_output on/off
  cmd.servo    = doc["servo"]    | "";    // set_servo name
  cmd.position = doc["position"] | "";    // set_servo position

  // Exempt from de-dup: queries (their reply carries the data, so they must
  // re-run) and the chunked-upload verbs (which reuse one id by design and use a
  // synchronous handshake, never the retry path).
  bool dedupExempt =
      (cmd.name[0] == 'q') ||
      (cmd.name[0] == 'g' && cmd.name[1] == 'e' && cmd.name[2] == 't') ||
      PNP_STREQ(cmd.name, "begin_transfer") ||
      PNP_STREQ(cmd.name, "program_chunk")  ||
      PNP_STREQ(cmd.name, "end_transfer");
  if (cmd.id > 0 && !dedupExempt && sm_.isDuplicateCommand(cmd.id)) {
    JsonDocument dup;            // re-ack a retry; state unchanged, skip status
    dup["type"] = "ack";
    dup["id"]   = cmd.id;
    dup["cmd"]  = cmd.name;
    serializeJson(dup, *io_);
    io_->println();
    return;
  }

  Response r = sm_.handleCommand(cmd, nowMs);
  if (r.kind == Response::Ack && cmd.id > 0 && !dedupExempt) sm_.rememberCommand(cmd.id);
  sendResponse(r);
  sendStatus(false);   // immediate state feedback; ToF rides the periodic push
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
  if (r.hasTofReadings) {
      JsonArray arr = out["tof"].to<JsonArray>();
      for (int c = 0; c < 6; c++) {
          JsonObject e = arr.add<JsonObject>();
          e["ch"]      = c;
          e["dist_mm"] = r.tofDistMm[c];
          e["valid"]   = r.tofValid[c];
      }
  }
  serializeJson(out, *io_);
  io_->println();
}

void Protocol::sendStatus(bool withTof) {
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
  out["outputs"]["pump"]            = s.pump;
  out["outputs"]["valve"]           = s.valve;
  out["outputs"]["servo_door"]      = s.servoDoor;
  out["outputs"]["servo_laser_btn"] = s.servoLaserBtn;
  out["inputs"]["estop_hw"]  = s.estopHw;
  out["inputs"]["start_btn"] = s.startBtn;
  out["inputs"]["pause_btn"] = s.pauseBtn;
  // Live ToF distances ride in the status broadcast (push) so the GUI doesn't
  // need a separate query_sensors poll — which kept the MCU RX buffer busy and
  // occasionally dropped user commands. Included every kStatusTofEvery-th frame
  // to bound outbound traffic; the GUI holds its last reading on skipped frames.
  if (withTof && (kStatusTofEvery <= 1 || (statusCount_ % kStatusTofEvery) == 0)) {
    float td[6]; bool tv[6];
    sm_.readTof(td, tv);
    JsonArray tof = out["tof"].to<JsonArray>();
    for (int c = 0; c < 6; c++) {
      JsonObject o = tof.add<JsonObject>();
      o["ch"]      = c;
      o["dist_mm"] = td[c];
      o["valid"]   = tv[c];
    }
  }
  statusCount_++;
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