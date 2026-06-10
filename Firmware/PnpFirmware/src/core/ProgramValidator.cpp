#include "ProgramValidator.h"
#include "../platform/Platform.h"
#include <string.h>
#include <stdio.h>

// All op-name string literals use PNP_STREQ so they live in flash on AVR.
// The kRequired table is eliminated entirely — op names and field names are
// checked inline with a PNP_STREQ chain, saving ~450 bytes of SRAM.

namespace pnp {

// ============================================================
// Public API
// ============================================================

bool ProgramValidator::validate(JsonObjectConst root, char* err, size_t errLen) {
    err[0] = '\0';
    if (root.isNull()) {
        PNP_SNPRINTF(err, errLen, "Program must be a JSON object");
        return false;
    }
    if (root["version"].as<int>() != 1) {
        PNP_SNPRINTF(err, errLen, "Missing or unsupported 'version' (expected 1)");
        return false;
    }
    JsonArrayConst body = root["program"];
    if (body.isNull()) {
        PNP_SNPRINTF(err, errLen, "Missing or invalid 'program' array");
        return false;
    }
    JsonObjectConst subs = root["subroutines"] | JsonObjectConst();
    checkBody(body, subs, err, errLen, "");
    if (err[0]) return false;

    for (JsonPairConst kv : subs) {
        if (err[0]) break;
        char prefix[48];
        PNP_SNPRINTF(prefix, sizeof(prefix), "subroutine '%s': ", kv.key().c_str());
        checkBody(kv.value().as<JsonArrayConst>(), subs, err, errLen, prefix);
    }
    return err[0] == '\0';
}

// Convenience overload for host tests — parses JSON then validates.
bool ProgramValidator::validate(const char* jsonStr, char* err, size_t errLen) {
    err[0] = '\0';
    JsonDocument doc;
    if (deserializeJson(doc, jsonStr)) {
        PNP_SNPRINTF(err, errLen, "Program must be a JSON object");
        return false;
    }
    return validate(doc.as<JsonObjectConst>(), err, errLen);
}

// ============================================================
// checkBody — PNP_STREQ chain replaces the kRequired table
// ============================================================

// Inline helper: set err if field is absent from instr.
// REQ() checks inside the PNP_STREQ chain below.
#define REQ(field) \
    if (!err[0] && instr[(field)].isNull()) \
        PNP_SNPRINTF(err, errLen, "%s: %s missing required field '%s'", loc, op, (field))

void ProgramValidator::checkBody(JsonArrayConst body, JsonObjectConst subs,
                                  char* err, size_t errLen, const char* prefix) {
    if (body.isNull()) {
        PNP_SNPRINTF(err, errLen, "%sbody must be an array", prefix);
        return;
    }
    int idx = 0;
    for (JsonVariantConst item : body) {
        if (err[0]) return;
        char loc[80];
        PNP_SNPRINTF(loc, sizeof(loc), "%sinstruction %d", prefix, idx++);

        JsonObjectConst instr = item.as<JsonObjectConst>();
        if (instr.isNull()) {
            PNP_SNPRINTF(err, errLen, "%s: must be an object", loc);
            continue;
        }
        const char* op = instr["op"] | "";
        if (!op[0]) { PNP_SNPRINTF(err, errLen, "%s: missing 'op'", loc); continue; }

        // Required-field check — replaces the kRequired table.
        // Every op-name literal goes to flash via PNP_STREQ on AVR.
        if      (PNP_STREQ(op, "MOVE"))        { REQ("x"); REQ("y"); REQ("z"); }
        else if (PNP_STREQ(op, "PROBE_Z"))     { REQ("x"); REQ("y"); REQ("store"); }
        else if (PNP_STREQ(op, "HOME"))        { REQ("axes"); }
        else if (PNP_STREQ(op, "OUTPUT"))      { REQ("name"); REQ("value"); }
        else if (PNP_STREQ(op, "READ_SENSOR")) { REQ("sensor"); REQ("store"); }
        else if (PNP_STREQ(op, "WAIT"))        { REQ("condition"); }
        else if (PNP_STREQ(op, "DELAY"))       { REQ("ms"); }
        else if (PNP_STREQ(op, "LOOP_WHILE"))  { REQ("condition"); REQ("body"); }
        else if (PNP_STREQ(op, "LOOP_FOR"))    { REQ("count"); REQ("body"); }
        else if (PNP_STREQ(op, "IF"))          { REQ("condition"); REQ("then"); }
        else if (PNP_STREQ(op, "CALL"))        { REQ("sub"); }
        else if (PNP_STREQ(op, "RETURN"))      { /* no required fields */ }
        else if (PNP_STREQ(op, "HALT"))        { /* no required fields */ }
        else if (PNP_STREQ(op, "FAULT"))       { REQ("reason"); }
        else if (PNP_STREQ(op, "SET_VAR"))     { REQ("name"); }
        else if (PNP_STREQ(op, "LOG"))         { REQ("message"); }
        else if (PNP_STREQ(op, "LABEL"))       { REQ("name"); }
        else if (PNP_STREQ(op, "JUMP"))        { REQ("to"); }
        else {
            PNP_SNPRINTF(err, errLen, "%s: unknown op '%s'", loc, op);
        }
        if (err[0]) return;

        // CALL: validate subroutine exists.
        if (PNP_STREQ(op, "CALL")) {
            const char* sub = instr["sub"] | "";
            if (sub[0] && subs[sub].isNull())
                PNP_SNPRINTF(err, errLen, "%s: CALL references unknown subroutine '%s'", loc, sub);
        }
        if (err[0]) return;

        // Recurse into nested bodies.
        static const char* kNested[] = {"body", "then", "else"};
        for (int ni = 0; ni < 3; ni++) {
            const char* nested = kNested[ni];
            if (!instr[nested].isNull()) {
                char nestedPrefix[128];
                PNP_SNPRINTF(nestedPrefix, sizeof(nestedPrefix), "%s/%s: ", loc, nested);
                checkBody(instr[nested].as<JsonArrayConst>(), subs, err, errLen, nestedPrefix);
                if (err[0]) return;
            }
        }
    }
}

#undef REQ

}  // namespace pnp
