# Host build of the REAL firmware protocol stack (no Mega required).
#
#   make            build ./pnp_firmware_host
#   make clean
#
# Usage:
#   echo '{"cmd":"query_status"}' | ./pnp_firmware_host
#   ./pnp_firmware_host < a_script_of_ndjson_commands.txt
#
# It runs the same Protocol + StateMachine + StubMachine the Mega runs, wired to
# stdin/stdout instead of a UART, so it emits the firmware's real wire format.
# Use it to diff firmware responses against the Python simulator and catch
# protocol drift before touching hardware.

CXX      ?= g++
SRC      := ../PnpFirmware/src
AJ       := ../test/ArduinoJson-7.3.1/src

CXXFLAGS := -std=c++17 -Wall -Wextra -I. -I$(SRC) -I$(AJ) \
            -DARDUINOJSON_ENABLE_ARDUINO_PRINT=1 \
            -DARDUINOJSON_ENABLE_ARDUINO_STRING=0 \
            -DARDUINOJSON_ENABLE_ARDUINO_STREAM=0 \
            -DARDUINOJSON_ENABLE_PROGMEM=0

SRCS := $(SRC)/protocol/Protocol.cpp \
        $(SRC)/core/StateMachine.cpp \
        $(SRC)/core/Interpreter.cpp \
        $(SRC)/core/ProgramValidator.cpp \
        $(SRC)/core/ProgramStore.cpp \
        $(SRC)/config/Config.cpp \
        main_host.cpp

pnp_firmware_host: $(SRCS) Arduino.h
	$(CXX) $(CXXFLAGS) $(SRCS) -o $@ -lm

clean:
	rm -f pnp_firmware_host

.PHONY: clean
