# Host-based unit tests for the portable firmware logic.
# Builds the same .cpp files Arduino IDE compiles for the Mega, but with g++.
#
#   make        build and run all tests
#   make clean

CXX      ?= g++
SRC_DIR  := ../PnpFirmware/src
AJ_DIR   := ArduinoJson-7.3.1/src
CXXFLAGS := -std=c++17 -Wall -Wextra -I$(SRC_DIR) -I$(AJ_DIR) -Iunity -I.

LOGIC    := $(SRC_DIR)/core/StateMachine.cpp \
            $(SRC_DIR)/core/Interpreter.cpp \
            $(SRC_DIR)/core/ProgramValidator.cpp \
            $(SRC_DIR)/core/ProgramStore.cpp \
            $(SRC_DIR)/config/Config.cpp

UNITY    := unity/unity.c

all: test_state_machine test_interpreter test_config
	./test_state_machine
	./test_interpreter
	./test_config

test_state_machine: $(LOGIC) test_state_machine.cpp $(UNITY)
	$(CXX) $(CXXFLAGS) $^ -o $@ -lm

test_interpreter: $(LOGIC) test_interpreter.cpp $(UNITY)
	$(CXX) $(CXXFLAGS) $^ -o $@ -lm

test_config: $(SRC_DIR)/config/Config.cpp test_config.cpp $(UNITY)
	$(CXX) $(CXXFLAGS) $^ -o $@ -lm

clean:
	rm -f test_state_machine test_interpreter test_config

.PHONY: all clean
