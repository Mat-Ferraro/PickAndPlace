// Host Unity tests for Config — CRC16, load/save round-trip, version checking,
// corruption detection, and all calibration fields including single-Y and
// the v4 per-axis soft travel limits.

#include "unity.h"
#include "core/../config/Config.h"
#include <string.h>

using namespace pnp;

void setUp(void)    { Config::clearTestEeprom(); }
void tearDown(void) {}

// ---- CRC ----

void test_crc_of_empty_buffer_is_nonzero(void) {
    uint16_t c = Config::computeCrc(nullptr, 0);
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, c);
}

void test_crc_changes_when_data_changes(void) {
    uint8_t a[] = {0x01, 0x02, 0x03};
    uint8_t b[] = {0x01, 0x02, 0x04};
    TEST_ASSERT_NOT_EQUAL(Config::computeCrc(a,3), Config::computeCrc(b,3));
}

void test_crc_is_deterministic(void) {
    uint8_t data[] = {0xDE, 0xAD, 0xBE, 0xEF};
    TEST_ASSERT_EQUAL(Config::computeCrc(data,4), Config::computeCrc(data,4));
}

// ---- isValid ----

void test_default_config_is_invalid(void) {
    Config cfg;
    TEST_ASSERT_FALSE(cfg.isValid());
}

void test_config_is_valid_after_updateCrc(void) {
    Config cfg;
    cfg.updateCrc();
    TEST_ASSERT_TRUE(cfg.isValid());
}

void test_version5_config_has_correct_version(void) {
    Config cfg;
    cfg.updateCrc();
    TEST_ASSERT_EQUAL(5, cfg.version);
    TEST_ASSERT_TRUE(cfg.isValid());
}

// ---- save / load round-trip ----

void test_save_then_load_round_trips_defaults(void) {
    Config a;
    a.save();

    Config b;
    TEST_ASSERT_TRUE(b.load());
    TEST_ASSERT_EQUAL_FLOAT(a.stepsPerMmX, b.stepsPerMmX);
    TEST_ASSERT_EQUAL_FLOAT(a.stepsPerMmY, b.stepsPerMmY);
    TEST_ASSERT_EQUAL_FLOAT(a.stepsPerMmZ, b.stepsPerMmZ);
    TEST_ASSERT_EQUAL_FLOAT(a.probeStepMm,  b.probeStepMm);
}

void test_all_four_stepper_axes_persist(void) {
    Config a;
    a.stepsPerMmX = 80.0f;
    a.stepsPerMmY = 80.0f;
    a.stepsPerMmZ = 32.0f;
    a.save();

    Config b;
    b.load();
    TEST_ASSERT_EQUAL_FLOAT(80.0f, b.stepsPerMmX);
    TEST_ASSERT_EQUAL_FLOAT(80.0f, b.stepsPerMmY);
    TEST_ASSERT_EQUAL_FLOAT(32.0f, b.stepsPerMmZ);
}

void test_y_is_single_shared_value(void) {
    // Both gantry motors share one steps/mm; calibrating Y stores one value.
    Config a;
    a.stepsPerMmY = 80.1f;
    a.save();

    Config b;
    b.load();
    TEST_ASSERT_EQUAL_FLOAT(80.1f, b.stepsPerMmY);
}

// ---- soft travel limits (v4) ----

void test_max_travel_defaults_to_zero(void) {
    Config cfg;
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cfg.maxTravelMmX);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cfg.maxTravelMmY);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, cfg.maxTravelMmZ);
    TEST_ASSERT_FALSE(cfg.hasTravelLimits());
}

void test_max_travel_persists(void) {
    Config a;
    a.maxTravelMmX = 420.0f;
    a.maxTravelMmY = 380.0f;
    a.maxTravelMmZ = 120.0f;
    a.save();

    Config b;
    b.load();
    TEST_ASSERT_EQUAL_FLOAT(420.0f, b.maxTravelMmX);
    TEST_ASSERT_EQUAL_FLOAT(380.0f, b.maxTravelMmY);
    TEST_ASSERT_EQUAL_FLOAT(120.0f, b.maxTravelMmZ);
    TEST_ASSERT_TRUE(b.hasTravelLimits());
}

void test_has_travel_limits_requires_all_three(void) {
    Config cfg;
    cfg.maxTravelMmX = 420.0f;
    cfg.maxTravelMmY = 380.0f;
    // Z still zero
    TEST_ASSERT_FALSE(cfg.hasTravelLimits());
    cfg.maxTravelMmZ = 120.0f;
    TEST_ASSERT_TRUE(cfg.hasTravelLimits());
}

void test_is_ready_for_motion_requires_cal_and_limits(void) {
    Config cfg;
    // Calibrated but no limits → not ready.
    cfg.stepsPerMmX=80.0f; cfg.stepsPerMmY=80.0f; cfg.stepsPerMmZ=32.0f;
    TEST_ASSERT_TRUE(cfg.isCalibrated());
    TEST_ASSERT_FALSE(cfg.isReadyForMotion());
    // Add limits → ready.
    cfg.maxTravelMmX=420.0f; cfg.maxTravelMmY=380.0f; cfg.maxTravelMmZ=120.0f;
    TEST_ASSERT_TRUE(cfg.isReadyForMotion());
}

void test_servo_angles_persist(void) {
    Config a;
    a.servoDoorOpen = 95.0f; a.servoDoorClosed = 10.0f;
    a.servoLaserBtnPress = 50.0f; a.servoLaserBtnRelease = 5.0f;
    a.save();

    Config b; b.load();
    TEST_ASSERT_EQUAL_FLOAT(95.0f, b.servoDoorOpen);
    TEST_ASSERT_EQUAL_FLOAT(10.0f, b.servoDoorClosed);
    TEST_ASSERT_EQUAL_FLOAT(50.0f, b.servoLaserBtnPress);
    TEST_ASSERT_EQUAL_FLOAT( 5.0f, b.servoLaserBtnRelease);
}

void test_probe_params_persist(void) {
    Config a;
    a.probeStepMm = 0.25f; a.probeMaxDepthMm = 150.0f; a.probeThreshMm = 3.0f;
    a.save();
    Config b; b.load();
    TEST_ASSERT_EQUAL_FLOAT(  0.25f, b.probeStepMm);
    TEST_ASSERT_EQUAL_FLOAT(150.0f,  b.probeMaxDepthMm);
    TEST_ASSERT_EQUAL_FLOAT(  3.0f,  b.probeThreshMm);
}

// ---- ToF offsets ----

void test_tof_offsets_default_to_uncalibrated(void) {
    Config cfg;
    for (int i = 0; i < 4; i++)
        TEST_ASSERT_EQUAL_FLOAT(-1.0f, cfg.tofOffsetMm[i]);
    TEST_ASSERT_FALSE(cfg.isSensorCalibrated(0));
}

void test_tof_offsets_persist(void) {
    Config a;
    a.tofOffsetMm[0]=45.0f; a.tofOffsetMm[1]=47.0f;
    a.tofOffsetMm[2]=46.0f; a.tofOffsetMm[3]=48.0f;
    a.save();

    Config b; b.load();
    TEST_ASSERT_EQUAL_FLOAT(45.0f, b.tofOffsetMm[0]);
    TEST_ASSERT_EQUAL_FLOAT(47.0f, b.tofOffsetMm[1]);
    TEST_ASSERT_EQUAL_FLOAT(46.0f, b.tofOffsetMm[2]);
    TEST_ASSERT_EQUAL_FLOAT(48.0f, b.tofOffsetMm[3]);
}

void test_is_sensor_calibrated_after_setting_offset(void) {
    Config cfg;
    cfg.tofOffsetMm[2] = 46.0f;
    TEST_ASSERT_TRUE(cfg.isSensorCalibrated(2));
    TEST_ASSERT_FALSE(cfg.isSensorCalibrated(0));
}

// ---- isCalibrated requires all 4 axes ----

void test_uncalibrated_is_false(void) {
    Config cfg;
    TEST_ASSERT_FALSE(cfg.isCalibrated());
}

void test_partial_calibration_is_false(void) {
    Config cfg;
    cfg.stepsPerMmX = 80.0f; cfg.stepsPerMmY = 80.0f;
    // Z still zero
    TEST_ASSERT_FALSE(cfg.isCalibrated());
}

void test_all_four_axes_calibrated(void) {
    Config cfg;
    cfg.stepsPerMmX=80.0f; cfg.stepsPerMmY=80.0f; cfg.stepsPerMmZ=32.0f;
    TEST_ASSERT_TRUE(cfg.isCalibrated());
}

// ---- corruption / version ----

void test_wrong_schema_version_fails_isValid(void) {
    Config cfg; cfg.version = 4; cfg.updateCrc();   // an older schema version
    TEST_ASSERT_FALSE(cfg.isValid());
}

void test_load_returns_false_on_empty_eeprom(void) {
    Config cfg;
    TEST_ASSERT_FALSE(cfg.load());
}

// ---- all calibrations together ----

void test_stepper_and_sensor_cal_persist_together(void) {
    Config a;
    a.stepsPerMmX=80.0f; a.stepsPerMmY=80.0f; a.stepsPerMmZ=32.0f;
    a.maxTravelMmX=420.0f; a.maxTravelMmY=380.0f; a.maxTravelMmZ=120.0f;
    a.tofOffsetMm[0]=45.0f;
    a.save();

    Config b; b.load();
    TEST_ASSERT_EQUAL_FLOAT(80.0f, b.stepsPerMmX);
    TEST_ASSERT_EQUAL_FLOAT(80.0f, b.stepsPerMmY);
    TEST_ASSERT_EQUAL_FLOAT(32.0f, b.stepsPerMmZ);
    TEST_ASSERT_EQUAL_FLOAT(420.0f, b.maxTravelMmX);
    TEST_ASSERT_EQUAL_FLOAT(380.0f, b.maxTravelMmY);
    TEST_ASSERT_EQUAL_FLOAT(120.0f, b.maxTravelMmZ);
    TEST_ASSERT_EQUAL_FLOAT(45.0f, b.tofOffsetMm[0]);
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_crc_of_empty_buffer_is_nonzero);
    RUN_TEST(test_crc_changes_when_data_changes);
    RUN_TEST(test_crc_is_deterministic);
    RUN_TEST(test_default_config_is_invalid);
    RUN_TEST(test_config_is_valid_after_updateCrc);
    RUN_TEST(test_version5_config_has_correct_version);
    RUN_TEST(test_save_then_load_round_trips_defaults);
    RUN_TEST(test_all_four_stepper_axes_persist);
    RUN_TEST(test_y_is_single_shared_value);
    RUN_TEST(test_max_travel_defaults_to_zero);
    RUN_TEST(test_max_travel_persists);
    RUN_TEST(test_has_travel_limits_requires_all_three);
    RUN_TEST(test_is_ready_for_motion_requires_cal_and_limits);
    RUN_TEST(test_servo_angles_persist);
    RUN_TEST(test_probe_params_persist);
    RUN_TEST(test_tof_offsets_default_to_uncalibrated);
    RUN_TEST(test_tof_offsets_persist);
    RUN_TEST(test_is_sensor_calibrated_after_setting_offset);
    RUN_TEST(test_uncalibrated_is_false);
    RUN_TEST(test_partial_calibration_is_false);
    RUN_TEST(test_all_four_axes_calibrated);
    RUN_TEST(test_wrong_schema_version_fails_isValid);
    RUN_TEST(test_load_returns_false_on_empty_eeprom);
    RUN_TEST(test_stepper_and_sensor_cal_persist_together);
    return UNITY_END();
}