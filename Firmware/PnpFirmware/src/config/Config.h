#pragma once

// TODO: EEPROM-backed persistence — named positions, calibration, servo angles,
// tunable params, AND the stored job program, each with a CRC + schema version.
// At boot, validate (CRC + schema + ProgramValidator) to set program_loaded.
// Mirrors the persistence rules in Documentation/open-decisions.md.

namespace pnp {
class Config {
  // bool load();  bool save();  bool programValid() const;
};
}  // namespace pnp
