#pragma once
#include <stdint.h>
#include <stddef.h>
#include <ArduinoJson.h>
#include "ProgramValidator.h"

// Owns the stored program: transfer state and parsed JsonDocument.
//
// The raw JSON accumulation buffer is heap-allocated in beginTransfer and
// freed immediately after endTransfer parses it — it costs zero BSS.
// ArduinoJson v7 copies all strings when given a const char* input, so the
// document is self-contained and safe to use after the buffer is freed.
//
// kMaxProgramBytes is a sanity cap on malloc size, not a BSS reservation.

namespace pnp {

class ProgramStore {
 public:
    static constexpr size_t kMaxProgramBytes = 4096;

    enum class Result : uint8_t {
        Ok, NoTransferInProgress, InvalidParam, OutOfOrder,
        BadBase64, SizeMismatch, Incomplete, JsonError,
        ValidationError, BufferFull,
    };

    Result beginTransfer(uint32_t size, uint16_t chunks);
    Result receiveChunk(uint16_t index, const char* b64Data);
    Result endTransfer(char* errOut, size_t errLen);
    void   reset();

    bool            programLoaded()  const { return programLoaded_; }
    JsonObjectConst program()        const { return doc_.as<JsonObjectConst>(); }
    int             instructionCount() const;
    size_t          programBytes()   const { return storedBytes_; }
    uint16_t        xferReceived()   const { return xferReceived_; }
    uint16_t        xferChunks()     const { return xferChunks_; }
    bool            transferInProgress() const { return xferSize_ > 0; }

 private:
    static uint8_t b64Val(char c);

    ProgramValidator validator_;
    JsonDocument     doc_;

    char*    buf_          = nullptr;  // heap-allocated during transfer only
    size_t   bufLen_       = 0;
    size_t   storedBytes_  = 0;        // byte count after successful load

    uint32_t xferSize_     = 0;
    uint16_t xferChunks_   = 0;
    uint16_t xferReceived_ = 0;
    bool     programLoaded_ = false;
};

}  // namespace pnp
