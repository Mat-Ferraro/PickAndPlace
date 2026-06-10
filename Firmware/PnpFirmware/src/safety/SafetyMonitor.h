#pragma once
#include "../core/StateMachine.h"

// TODO: continuous safety monitors (Documentation/architecture.md §11), polled
// every loop. Raise faults into the StateMachine via injectFault():
//   - laser-park interlock   -> "laser_interlock" / "laser_not_parked"
//   - pickup-loss detection  -> "pickup_lost"
//   - StallGuard stall/jam    -> "motion_fault" (per axis)
// Also owns the hardware E-stop edge -> StateMachine::setEstopHardware().

namespace pnp {
class SafetyMonitor {
  // explicit SafetyMonitor(StateMachine&, IMachine&);  void poll(uint32_t now);
};
}  // namespace pnp
