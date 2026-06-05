# Documentation Change Log

## v0.7

- Applied package-review fixes from the v0.6 audit.
- Moved `tmc2209_datasheet_rev1.09.pdf` into the held-reference list and added
  TMC2209 StepStick notes: STEP/DIR-first approach, UART optional later, current
  limit/orientation warnings, and external-driver fallback.
- Added the VL53L0X 8-bit vs 7-bit address warning: datasheet address `0x52` maps
  to Arduino/Wire address `0x29`.
- Added practical JSON serial limits: one JSON object per line, recommended maximum
  line length, status-rate limits, command timeout behavior, NACK rules for
  malformed/oversized packets, and job/config loading implications for headless
  operation.
- Added homing/limit-switch and dual-Y gantry-squaring decisions as high-impact
  motion items.
- Added persistent-config implementation notes: schema version, CRC/checksum,
  defaults, explicit save, invalid-config behavior, and EEPROM wear limits.
- Cleaned up README bring-up wording and replaced unstable Amazon `clp` links with
  stable product/listing URLs where known.

## v0.6

- **Reorganized** the single `..._v0_5.md` document into a focused set: `README.md`,
  `architecture.md`, `pin-mapping.md`, `communication-protocol.md`,
  `components-and-references.md`, `open-decisions.md`, and this changelog.
- Separated settled design (architecture) from the live decision log and promoted
  the JSON serial protocol to a first-class contract document.
- Folded in datasheet-verified specs from the four reference PDFs now on hand
  (Arduino Mega pinout, RAMPS 1.4 manual, TCA9548A, VL53L0X):
  - **VL53L0X:** confirmed ~2.8 V part, signal pins 3.6 V-max (not 5 V-tolerant),
    fixed default I2C address 0x52, XSHUT/GPIO1 behavior, timing/current, and the
    calibration set. Resolves the old "sensor voltage note."
  - **TCA9548A:** address range 0x70–0x77 (build uses 0x70), single 8-bit
    channel-bitmask register, 5 V-tolerant, voltage-translation capable. Added a
    design note on using the mux to bridge the 5 V Mega bus to 3.3 V sensors.
  - **Mega:** confirmed I2C pins, the four free hardware-interrupt pins
    (D2/D3/D18/D19), and the 20 mA/pin and 50 mA/3.3 V current ceilings.
  - **RAMPS:** identified the power MOSFETs as logic-level STP55NF06L and noted the
    ~5 A/~11 A polyfuses — informs the pump-driver decision.
- Added a reference-documentation index tracking PDFs on hand vs. still needed, and
  noted that VL53L0X control requires the API user manual UM2039.
- Flagged the unresolved gantry-vs-arm question as a blocking item.

## v0.5

- Added purchased/candidate module inventory; relay, L298N, and SSD1309 notes;
  pump/valve driver comparison; local-display section; expanded vendor checklist;
  architecture diagram updated for upstream I2C OLED + TCA9548A mux.

## v0.4

- Added verified bench bring-up status; power architecture; RAMPS pin mapping;
  USB serial as the primary link; E-stop hardware-vs-software distinction; TMC2209
  orientation/current-limit warnings; ToF/TCA9548A integration; firmware
  architecture and persistent-config sections; vendor documentation list.

## v0.3

- Initial system architecture: Mega/RAMPS, 4 steppers, 6 ToF sensors, headless
  operation, GUI responsibilities, JSON protocol, high-level states.
