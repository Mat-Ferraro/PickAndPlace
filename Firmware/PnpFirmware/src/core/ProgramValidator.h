#pragma once
#include <stdint.h>
#include <stddef.h>
#include <ArduinoJson.h>

// C++ port of ProgramValidator from Software/interpreter.py.
// Two overloads:
//   validate(JsonObjectConst, ...) — primary; used by ProgramStore which
//       already has the parsed document (avoids a second JSON parse).
//   validate(const char*, ...)    — convenience wrapper for host tests.

namespace pnp {

class ProgramValidator {
 public:
    bool validate(JsonObjectConst root, char* err, size_t errLen);
    bool validate(const char* jsonStr, char* err, size_t errLen); // test helper

 private:
    void checkBody(JsonArrayConst body, JsonObjectConst subs,
                   char* err, size_t errLen, const char* prefix);
};

}  // namespace pnp
