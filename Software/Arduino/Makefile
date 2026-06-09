# Host-based unit tests for the portable firmware logic.
# Builds the same .cpp files Arduino IDE compiles for the Mega, but with g++ on
# the PC, linked against a MockMachine and Unity. This is the test harness that
# lives beside the sketch (Arduino IDE has no host test runner of its own).
#
#   make        build and run the tests
#   make clean

CXX      ?= g++
SRC_DIR  := ../PnpFirmware/src
CXXFLAGS := -std=c++17 -Wall -Wextra -I$(SRC_DIR) -Iunity -I.

# Portable logic under test (no Arduino.h, no ArduinoJson).
LOGIC    := $(SRC_DIR)/core/StateMachine.cpp

TESTS    := test_state_machine.cpp
UNITY    := unity/unity.c

BIN      := run_tests

all: test

$(BIN): $(LOGIC) $(TESTS) $(UNITY)
	$(CXX) $(CXXFLAGS) $^ -o $@

test: $(BIN)
	./$(BIN)

clean:
	rm -f $(BIN)

.PHONY: all test clean
