#pragma once
#include "IMachine.h"

// TODO: the REAL hardware HAL — the concrete IMachine implemented on the bench,
// one subsystem at a time, replacing StubMachine:
//   - steppers (X / Y1 / Y2 / Z) via AccelStepper + TMC2209 UART
//   - sensorless homing + StallGuard jam (DIAG)
//   - ToF (VL53L0X) through the TCA9548A mux
//   - servos (door, laser-button), pump/valve relay outputs
// This file is NOT host-unit-tested; it is validated on hardware (bring-up).

namespace pnp {
class Machine : public IMachine {
  // implements every IMachine method against real peripherals
};
}  // namespace pnp
