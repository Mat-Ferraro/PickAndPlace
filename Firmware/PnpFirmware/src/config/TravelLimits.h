#pragma once

// Per-axis soft travel envelope — the single source of the bounds-check math.
//
// This is deliberately dependency-free (just floats and a pure function) so it
// can be included from both config/ (where the limits live, in Config) and
// core/ (where the Interpreter enforces them) without creating a layering
// cycle. Config builds one of these from its maxTravelMm* fields; the
// StateMachine pushes it into the Interpreter at run_program time.
//
// Semantics:
//   - enabled == false  -> unbounded; offendingAxis() always returns nullptr.
//     This is the first-boot / unconfigured state (all maxTravelMm* == 0), so
//     existing motion behaviour is unchanged until an operator sets limits.
//   - enabled == true   -> a target faults if any axis is outside [0, max].
//
// The work-envelope is a backstop *within* the usable range. It is NOT the
// headless-readiness gate (that is Config::isReadyForMotion(), which also
// requires calibration). A machine can move while attended with no limits set;
// limits become mandatory only for unattended READY.

namespace pnp {

struct TravelLimits {
    float maxX    = 0.0f;
    float maxY    = 0.0f;   // one envelope for the dual-Y gantry
    float maxZ    = 0.0f;
    bool  enabled = false;

    // Returns the offending axis label ("X", "Y", or "Z") if the target lies
    // outside [0, max] on that axis, else nullptr. The first violating axis in
    // X, Y, Z order is reported. A small epsilon tolerates float round-trip so
    // a move to exactly the configured limit is accepted.
    const char* offendingAxis(float x, float y, float z) const {
        if (!enabled) return nullptr;
        const float eps = 1e-3f;
        if (x < -eps || x > maxX + eps) return "X";
        if (y < -eps || y > maxY + eps) return "Y";
        if (z < -eps || z > maxZ + eps) return "Z";
        return nullptr;
    }
};

}  // namespace pnp
