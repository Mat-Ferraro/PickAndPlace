// Arduino IDE 2.x / arduino-cli build options for this sketch.
// This file is NOT C source — it holds compiler flags the build appends
// globally (including the core), so it survives IDE/core upgrades and travels
// with the project (unlike editing the core's HardwareSerial.h).
//
// Enlarge the hardware serial buffers. The default 64-byte RX buffer is smaller
// than some of our JSON commands (~75 bytes), so when the loop is briefly
// blocked writing a status frame, an incoming command can overflow RX and be
// truncated -> dropped press. 256 RX comfortably holds any command (and a few
// queued) across a write stall; 256 TX lets bigger status frames drain with
// far less blocking. Cost: (256-64)*2 = 384 bytes RAM on the Mega's 8 KB.
-DSERIAL_RX_BUFFER_SIZE=256 -DSERIAL_TX_BUFFER_SIZE=256
